import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useEffect, useMemo, useRef, useState } from 'react'
import { lab } from '../api/queries'
import type { Gate, GateReport, Metrics, TradeDetail } from '../api/types'
import { EquityChart, TradeChart } from '../components/LabCharts'
import { Badge, Card, QueryState, Skeleton } from '../components/ui'
import { gainClass, REGIME_COLORS, STATUS_STYLE } from '../lib/colors'
import { label, num, pct, rupees } from '../lib/format'

function Stat({ name, value, tone }: { name: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-line bg-panel-2/40 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-muted">{name}</div>
      <div className={`tabular mt-0.5 text-lg font-semibold ${tone ?? ''}`}>{value}</div>
    </div>
  )
}

function MetricGrid({ m }: { m: Metrics }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Stat name="CAGR" value={pct(m.cagr, 1, true)} tone={gainClass(m.cagr)} />
      <Stat name="Max drawdown" value={pct(m.max_drawdown, 1)} tone="text-bad" />
      <Stat name="Calmar" value={num(m.calmar, 2)} />
      <Stat name="Sharpe" value={num(m.sharpe, 2)} />
      <Stat name="Sortino" value={num(m.sortino, 2)} />
      <Stat name="Exposure" value={pct(m.exposure, 0)} />
      <Stat name="Trades" value={num(m.trades)} />
      <Stat name="Win rate" value={pct(m.win_rate, 1)} />
      <Stat name="Avg win / loss" value={`${pct(m.avg_win, 1, true)} / ${pct(m.avg_loss, 1)}`} />
      <Stat name="Profit factor" value={num(m.profit_factor, 2)} />
      <Stat name="Avg holding" value={m.avg_days == null ? '—' : `${num(m.avg_days, 1)}d`} />
      <Stat name="Final equity" value={rupees(m.final_equity, 0)} tone={gainClass(m.total_return)} />
    </div>
  )
}

function GateDetail({ gate }: { gate: Gate }) {
  const v = gate.value as Record<string, any> | number | null // eslint-disable-line @typescript-eslint/no-explicit-any
  if (gate.name === 'out_of_sample' && v && typeof v === 'object') {
    const rows = [['In-sample', v.in_sample], ['Out-of-sample', v.out_of_sample]] as const
    return (
      <table className="tabular mt-2 w-full text-xs">
        <thead><tr className="text-left text-muted"><th>Period</th><th className="text-right">CAGR</th><th className="text-right">Max DD</th><th className="text-right">Calmar</th><th className="text-right">Sharpe</th></tr></thead>
        <tbody>
          {rows.map(([name, r]) => (
            <tr key={name} className="border-t border-line/60"><td className="py-1">{name}</td><td className="text-right">{pct(r.cagr, 1, true)}</td><td className="text-right">{pct(r.max_drawdown, 1)}</td><td className="text-right">{num(r.calmar, 2)}</td><td className="text-right">{num(r.sharpe, 2)}</td></tr>
          ))}
        </tbody>
      </table>
    )
  }
  if (gate.name === 'parameter_sensitivity' && v && typeof v === 'object') {
    return (
      <table className="tabular mt-2 w-full text-xs">
        <thead><tr className="text-left text-muted"><th>Parameter</th><th className="text-right">Tested</th><th className="text-right">Calmar</th><th className="text-right">Drop</th></tr></thead>
        <tbody>
          {(v.variants as { param: string; value: number; calmar: number | null; drop: number | null }[]).map((s) => (
            <tr key={`${s.param}-${s.value}`} className="border-t border-line/60"><td className="py-1">{s.param}</td><td className="text-right">{num(s.value, 2)}</td><td className="text-right">{num(s.calmar, 2)}</td><td className={`text-right ${(s.drop ?? 0) > 0.4 ? 'text-bad' : ''}`}>{pct(s.drop, 0)}</td></tr>
          ))}
        </tbody>
      </table>
    )
  }
  if (v && typeof v === 'object') {
    return <p className="mt-1 text-xs text-muted">{Object.entries(v).map(([k, val]) => `${k}: ${typeof val === 'number' ? num(val, 2) : String(val)}`).join(' · ')}</p>
  }
  return v == null ? null : <p className="mt-1 text-xs text-muted">Value: {typeof v === 'number' ? (Number.isInteger(v) ? v : num(v, 3)) : String(v)}</p>
}

function Gates({ report }: { report: GateReport }) {
  return (
    <Card title="Anti-overfitting gates" right={<Badge className={STATUS_STYLE[report.passed_all ? 'PASS' : 'FAIL']}>{report.passed_all ? 'All passed' : 'Not cleared'}</Badge>}>
      {report.review_required && (
        <p className="mb-3 rounded border border-warn/50 bg-warn/10 px-3 py-2 text-sm text-warn">Manual review required: {report.review_reason}.</p>
      )}
      <ul className="space-y-3">
        {report.gates.map((g) => {
          const status = g.passed == null ? 'UNKNOWN' : g.passed ? 'PASS' : 'FAIL'
          return (
            <li key={g.name} className="rounded-lg border border-line p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{label(g.name)}</span>
                <Badge className={STATUS_STYLE[status]}>{status}</Badge>
              </div>
              <p className="text-xs text-muted">Requirement: {g.threshold}{g.detail ? ` · ${g.detail}` : ''}</p>
              <GateDetail gate={g} />
            </li>
          )
        })}
      </ul>
    </Card>
  )
}

function Fact({ name, children }: { name: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-muted">{name}</div>
      <div className="tabular mt-0.5 text-sm font-medium">{children}</div>
    </div>
  )
}

function TradeFacts({ d }: { d: TradeDetail }) {
  const t = d.trade
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
        <Fact name="Symbol">{t.symbol}</Fact>
        <Fact name="Sector">{t.sector ?? '—'}</Fact>
        <Fact name="Result"><span className={gainClass(t.ret)}>{pct(t.ret, 1, true)}</span> · {t.days}d · {rupees(t.pnl, 0)}</Fact>
        <Fact name="Entry">{rupees(t.entry_price)} · {t.entry_date}</Fact>
        <Fact name="Initial stop"><span className="text-bad">{rupees(t.initial_stop)}</span></Fact>
        <Fact name="Exit">{rupees(t.exit_price)} · {t.exit_date}</Fact>
        <Fact name="Final stop">{rupees(t.final_stop)} {t.be_activated ? '· breakeven active' : ''}</Fact>
        <Fact name="Max adverse (MAE)"><span className="text-bad">{pct(t.mae, 1)}</span> · day {t.mae_day}</Fact>
        <Fact name="Max favourable (MFE)"><span className="text-good">{pct(t.mfe, 1, true)}</span> · day {t.mfe_day}</Fact>
        <Fact name="Peak price">{rupees(t.peak_price)} · giveback {pct(t.giveback, 1)}</Fact>
        <Fact name="Regime at entry"><span style={{ color: REGIME_COLORS[t.regime_at_entry ?? ''] }}>{t.regime_at_entry ?? 'unknown'}</span></Fact>
        <Fact name="Exit reason">{t.exit_reason}</Fact>
      </div>
      <div className="space-y-2 text-sm">
        <div className="rounded-lg border border-line bg-panel-2/40 p-3"><div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">How it came onto the radar & entry</div>{d.explanation.radar}</div>
        <div className="rounded-lg border border-line bg-panel-2/40 p-3"><div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Trail & exit</div>{d.explanation.exit}</div>
      </div>
    </div>
  )
}

function TradeJournal({ runId, years }: { runId: string; years: number[] }) {
  const [year, setYear] = useState<number | undefined>()
  const [reason, setReason] = useState<string | undefined>()
  const [sort, setSort] = useState<'date' | 'pnl'>('date')
  const [selected, setSelected] = useState<number | null>(null)
  const trades = useQuery({ ...lab.trades(runId, { year, exit_reason: reason, sort }), placeholderData: keepPreviousData })
  const rows = useMemo(() => trades.data?.data ?? [], [trades.data])
  useEffect(() => {
    if (rows.length && !rows.some((r) => r.trade_id === selected)) setSelected(rows[0]!.trade_id)
  }, [rows, selected])
  const detail = useQuery({ ...lab.trade(runId, selected ?? 0), enabled: selected != null })

  const parent = useRef<HTMLDivElement>(null)
  const virtual = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => 56, overscan: 8 })
  const select = 'rounded border border-line bg-panel-2 px-2 py-1 text-xs'

  return (
    <Card title="Trade journal" hint={`(${trades.data?.total ?? '…'} trades)`}>
      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        <div>
          <div className="mb-2 flex flex-wrap gap-2">
            <select aria-label="Year" className={select} value={year ?? ''} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : undefined)}>
              <option value="">All years</option>
              {years.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
            <select aria-label="Exit type" className={select} value={reason ?? ''} onChange={(e) => setReason(e.target.value || undefined)}>
              <option value="">All exits</option>
              {['stop', 'stop-gap', 'trail', 'time', 'end-of-test'].map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <select aria-label="Sort" className={select} value={sort} onChange={(e) => setSort(e.target.value as 'date' | 'pnl')}>
              <option value="date">Sort: date</option>
              <option value="pnl">Sort: P&L</option>
            </select>
          </div>
          <div ref={parent} className="h-[34rem] overflow-auto rounded border border-line" role="listbox" aria-label="Trades">
            <div style={{ height: virtual.getTotalSize(), position: 'relative' }}>
              {virtual.getVirtualItems().map((item) => {
                const t = rows[item.index]!
                const active = t.trade_id === selected
                return (
                  <button
                    key={t.trade_id}
                    type="button"
                    role="option"
                    aria-selected={active}
                    onClick={() => setSelected(t.trade_id)}
                    className={`absolute inset-x-0 border-b border-line/60 px-3 py-1.5 text-left ${active ? 'bg-panel-2' : 'hover:bg-panel-2/60'}`}
                    style={{ height: item.size, transform: `translateY(${item.start}px)` }}
                  >
                    <div className="flex items-center justify-between"><span className="font-semibold">{t.symbol}</span><span className={`tabular text-sm ${gainClass(t.ret)}`}>{pct(t.ret, 1, true)}</span></div>
                    <div className="flex items-center justify-between text-[11px] text-muted"><span>{t.entry_date} · {t.days}d</span><span>{t.exit_reason}</span></div>
                  </button>
                )
              })}
            </div>
          </div>
        </div>
        <div className="min-w-0">
          <QueryState query={detail} empty="Select a trade." skeleton={<Skeleton className="h-96" />}>
            {(res) => (
              <div className="space-y-4">
                <div className="flex items-baseline gap-3"><h3 className="text-lg font-semibold">{res.data.trade.symbol}</h3><span className="text-sm text-muted">{res.data.trade.entry_date} → {res.data.trade.exit_date}</span><span className={`tabular ml-auto text-lg font-semibold ${gainClass(res.data.trade.ret)}`}>{pct(res.data.trade.ret, 1, true)}</span></div>
                <TradeChart detail={res.data} />
                <TradeFacts d={res.data} />
              </div>
            )}
          </QueryState>
        </div>
      </div>
    </Card>
  )
}

export function RunPage({ runId }: { runId: string }) {
  const run = useQuery(lab.run(runId))
  const equity = useQuery(lab.equity(runId))
  return (
    <QueryState query={run} empty="This run does not exist." skeleton={<Skeleton className="h-96" />}>
      {(res) => {
        const { run: info, metrics, gates } = res.data
        const years = metrics.by_year.map((y) => y.year).reverse()
        return (
          <div className="space-y-4">
            <div className="flex flex-wrap items-baseline gap-3">
              <Link to="/strategies" search={((prev: { as_of?: string }) => ({ as_of: prev.as_of })) as never} className="text-sm text-accent hover:underline">← Strategies</Link>
              <h1 className="text-xl font-bold">{info.strategy.label || info.strategy_id}</h1>
              <Badge className="border-line bg-panel-2 text-muted">{info.strategy.status}</Badge>
              <span className="text-sm text-muted">{info.start} → {info.end} · {info.sessions} sessions · run {info.run_id}</span>
            </div>
            <Card title="Performance"><MetricGrid m={metrics} /></Card>
            <Card title="Equity & drawdown">
              <QueryState query={equity} skeleton={<Skeleton className="h-96" />}>{(e) => <EquityChart data={e.data} />}</QueryState>
            </Card>
            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Returns by year">
                <table className="tabular w-full text-sm"><tbody>{metrics.by_year.map((y) => (<tr key={y.year} className="border-b border-line/60 last:border-0"><td className="py-1.5">{y.year}</td><td className={`text-right ${gainClass(y.return)}`}>{pct(y.return, 1, true)}</td></tr>))}</tbody></table>
              </Card>
              <Card title="Performance by market regime at entry">
                <table className="tabular w-full text-sm">
                  <thead><tr className="text-left text-[11px] uppercase tracking-wide text-muted"><th>Regime</th><th className="text-right">Trades</th><th className="text-right">Win</th><th className="text-right">Avg</th><th className="text-right">P&L</th></tr></thead>
                  <tbody>{metrics.by_regime.map((r) => (<tr key={r.regime} className="border-t border-line/60"><td className="py-1.5" style={{ color: REGIME_COLORS[r.regime] }}>{r.regime}</td><td className="text-right">{r.trades}</td><td className="text-right">{pct(r.win_rate, 0)}</td><td className={`text-right ${gainClass(r.avg_ret)}`}>{pct(r.avg_ret, 1, true)}</td><td className={`text-right ${gainClass(r.pnl)}`}>{rupees(r.pnl, 0)}</td></tr>))}</tbody>
                </table>
              </Card>
            </div>
            {gates ? <Gates report={gates} /> : <Card title="Anti-overfitting gates"><p className="text-sm text-muted">This run was started without gates.</p></Card>}
            <TradeJournal runId={runId} years={years} />
            <details className="rounded-lg border border-line bg-panel"><summary className="cursor-pointer px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Strategy definition used for this run</summary><pre className="overflow-auto border-t border-line p-4 text-xs text-muted">{JSON.stringify(info.strategy, null, 2)}</pre></details>
          </div>
        )
      }}
    </QueryState>
  )
}
