import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { type FormEvent, useState } from 'react'
import { type Envelope, post, send } from '../api/client'
import { lab, ops } from '../api/queries'
import type { PaperAccount } from '../api/types'
import { EquityChart } from '../components/LabCharts'
import { Badge, Card, QueryState, Skeleton } from '../components/ui'
import { gainClass } from '../lib/colors'
import { num, pct, rupees } from '../lib/format'

const keepAsOf = ((prev: { as_of?: string }) => ({ as_of: prev.as_of })) as never

function LiveSignals() {
  const strategies = useQuery(lab.strategies())
  const [chosen, setChosen] = useState('')
  const id = chosen || strategies.data?.data[0]?.id || ''
  const signals = useQuery(ops.liveSignals(id))
  return (
    <Card
      title="Live signals"
      hint="(candidates on the latest session; they would fill at the next open)"
      right={
        <select aria-label="Strategy" value={id} onChange={(e) => setChosen(e.target.value)} className="rounded border border-line bg-panel-2 px-2 py-1 text-xs">
          {strategies.data?.data.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
      }
    >
      <QueryState query={signals} skeleton={<Skeleton className="h-24" />} empty="Live signals need built analytics.">
        {(res) => (
          <div className="space-y-3">
            <p className="text-xs text-muted">
              Rule: <code className="text-text">{res.data.rule}</code> · regime now: <b className="text-text">{res.data.regime ?? 'unknown'}</b>
            </p>
            {res.data.candidates.length === 0 ? (
              <p className="text-sm text-muted">{res.data.note} No candidates today.</p>
            ) : (
              <div className="overflow-auto rounded border border-line">
                <table className="tabular w-full text-sm">
                  <thead className="bg-panel-2 text-[11px] uppercase text-muted"><tr><th className="px-3 py-1.5 text-left">#</th><th className="px-3 py-1.5 text-left">Symbol</th><th className="px-3 py-1.5 text-left">Sector</th><th className="px-3 py-1.5 text-right">Close</th><th className="px-3 py-1.5 text-right">21d</th><th className="px-3 py-1.5 text-right">63d</th><th className="px-3 py-1.5 text-right">RVol</th></tr></thead>
                  <tbody>
                    {res.data.candidates.map((c) => (
                      <tr key={c.symbol} className="border-t border-line/60">
                        <td className="px-3 py-1">{c.rank}</td>
                        <td className="px-3 py-1"><Link to="/stocks/$symbol" params={{ symbol: c.symbol }} search={keepAsOf} className="font-medium text-accent hover:underline">{c.symbol}</Link></td>
                        <td className="px-3 py-1 text-muted">{c.sector ?? '—'}</td>
                        <td className="px-3 py-1 text-right">{rupees(c.close)}</td>
                        <td className={`px-3 py-1 text-right ${gainClass(c.ret_21d ?? null)}`}>{pct(c.ret_21d, 1, true)}</td>
                        <td className={`px-3 py-1 text-right ${gainClass(c.ret_63d ?? null)}`}>{pct(c.ret_63d, 1, true)}</td>
                        <td className="px-3 py-1 text-right">{num(c.rvol20, 1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </QueryState>
    </Card>
  )
}

function NewAccount() {
  const client = useQueryClient()
  const strategies = useQuery(lab.strategies())
  const [strategyId, setStrategyId] = useState('')
  const [start, setStart] = useState('')
  const [capital, setCapital] = useState('')
  const create = useMutation({
    mutationFn: () =>
      post<Envelope<PaperAccount>>('/api/paper', {
        strategy_id: strategyId || strategies.data?.data[0]?.id,
        start_date: start || undefined,
        capital: capital ? Number(capital) : undefined,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['paper'] }),
  })
  const field = 'rounded border border-line bg-panel-2 px-2 py-1.5 text-sm'
  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <label className="text-xs text-muted">Strategy
        <select aria-label="New account strategy" className={`${field} mt-1 block`} value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
          {strategies.data?.data.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
      </label>
      <label className="text-xs text-muted">Start date (blank = today)
        <input aria-label="Start date" type="date" className={`${field} mt-1 block`} value={start} onChange={(e) => setStart(e.target.value)} />
      </label>
      <label className="text-xs text-muted">Capital (₹)
        <input aria-label="Capital" type="number" min={1} placeholder="strategy default" className={`${field} mt-1 block w-40`} value={capital} onChange={(e) => setCapital(e.target.value)} />
      </label>
      <button type="submit" disabled={create.isPending || !(strategyId || strategies.data?.data.length)} className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-bg disabled:opacity-50">Start paper account</button>
      {create.isError && <span className="text-xs text-bad" role="alert">{(create.error as Error).message}</span>}
    </form>
  )
}

export function PaperPage() {
  const client = useQueryClient()
  const accounts = useQuery(ops.paperAccounts())
  const remove = useMutation({
    mutationFn: (id: number) => send('DELETE', `/api/paper/${id}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['paper'] }),
  })
  return (
    <div className="space-y-4">
      <Card title="Paper accounts" hint="(forward tests: the strategy runs from a start date with the backtest engine; no real orders exist anywhere)">
        <NewAccount />
        <div className="mt-4">
          <QueryState query={accounts} isEmpty={(r) => r.data.length === 0} empty="No paper accounts yet. Start one above; use a past start date to replay it." skeleton={<Skeleton className="h-24" />}>
            {(res) => (
              <div className="overflow-auto rounded border border-line">
                <table className="tabular w-full text-sm">
                  <thead className="bg-panel-2 text-[11px] uppercase text-muted"><tr><th className="px-3 py-2 text-left">Account</th><th className="px-3 py-2 text-left">Since</th><th className="px-3 py-2 text-right">Equity</th><th className="px-3 py-2 text-right">Return</th><th className="px-3 py-2 text-right">Max DD</th><th className="px-3 py-2 text-right">Open</th><th className="px-3 py-2 text-right">Closed</th><th /></tr></thead>
                  <tbody>
                    {res.data.map((a) => (
                      <tr key={a.id} className="border-t border-line/60">
                        <td className="px-3 py-2"><Link to="/paper/$accountId" params={{ accountId: String(a.id) }} search={keepAsOf} className="font-medium text-accent hover:underline">{a.name}</Link>{a.error && <div className="text-xs text-bad">{a.error}</div>}</td>
                        <td className="px-3 py-2 text-muted">{a.start_date}</td>
                        <td className="px-3 py-2 text-right">{rupees(a.equity, 0)}</td>
                        <td className={`px-3 py-2 text-right ${gainClass(a.return ?? null)}`}>{pct(a.return, 2, true)}</td>
                        <td className="px-3 py-2 text-right text-bad">{pct(a.max_drawdown ?? null, 1)}</td>
                        <td className="px-3 py-2 text-right">{a.open_positions ?? '—'}</td>
                        <td className="px-3 py-2 text-right">{a.closed_trades ?? '—'}</td>
                        <td className="px-3 py-2 text-right"><button type="button" onClick={() => remove.mutate(a.id)} className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-bad">Delete</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </QueryState>
        </div>
      </Card>
      <LiveSignals />
    </div>
  )
}

export function PaperDetailPage({ accountId }: { accountId: number }) {
  const detail = useQuery(ops.paper(accountId))
  return (
    <QueryState query={detail} empty="This paper account does not exist." skeleton={<Skeleton className="h-96" />}>
      {(res) => {
        const { account, curve, open_positions: positions, next_orders: next, trades, metrics } = res.data
        return (
          <div className="space-y-4">
            <div className="flex flex-wrap items-baseline gap-3">
              <Link to="/paper" search={keepAsOf} className="text-sm text-accent hover:underline">← Paper accounts</Link>
              <h1 className="text-xl font-bold">{account.name}</h1>
              <span className="text-sm text-muted">{account.strategy_id} · since {account.start_date} · as of {res.as_of}</span>
              <span className={`tabular ml-auto text-lg font-semibold ${gainClass(account.return ?? null)}`}>{rupees(account.equity, 0)} ({pct(account.return, 2, true)})</span>
            </div>
            <Card title="Equity & drawdown"><EquityChart data={{ curve, benchmark: [] }} /></Card>
            <div className="grid gap-4 lg:grid-cols-2">
              <Card title="Orders for the next open" hint={`(${next.slots_free} slots free)`}>
                <div className="space-y-3 text-sm">
                  <div>
                    <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Buy (indicative)</h3>
                    {next.entries.length === 0 ? <p className="text-muted">No entries.</p> : (
                      <ul className="space-y-1">{next.entries.map((e) => <li key={e.symbol} className="flex justify-between"><span><b>{e.symbol}</b> <span className="text-muted">#{e.rank} · {e.sector}</span></span><span className="tabular">{num(e.indicative_shares)} @ ~{rupees(e.indicative_price)} · stop {rupees(e.initial_stop)}</span></li>)}</ul>
                    )}
                  </div>
                  <div>
                    <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Sell</h3>
                    {next.exits.length === 0 ? <p className="text-muted">No exits signalled.</p> : (
                      <ul className="space-y-1">{next.exits.map((e) => <li key={e.symbol}><b>{e.symbol}</b> <span className="text-muted">{e.detail}</span></li>)}</ul>
                    )}
                  </div>
                  <p className="text-xs text-muted">{next.note}</p>
                </div>
              </Card>
              <Card title="Performance"><dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
                <dt className="text-muted">CAGR</dt><dd className="tabular text-right">{pct(metrics.cagr, 1, true)}</dd>
                <dt className="text-muted">Max drawdown</dt><dd className="tabular text-right text-bad">{pct(metrics.max_drawdown, 1)}</dd>
                <dt className="text-muted">Sharpe</dt><dd className="tabular text-right">{num(metrics.sharpe, 2)}</dd>
                <dt className="text-muted">Closed trades</dt><dd className="tabular text-right">{metrics.trades}</dd>
                <dt className="text-muted">Win rate</dt><dd className="tabular text-right">{pct(metrics.win_rate, 0)}</dd>
                <dt className="text-muted">Exposure</dt><dd className="tabular text-right">{pct(metrics.exposure, 0)}</dd>
              </dl></Card>
            </div>
            <Card title="Open positions" hint={`(${positions.length})`}>
              {positions.length === 0 ? <p className="text-sm text-muted">No open positions.</p> : (
                <div className="overflow-auto rounded border border-line">
                  <table className="tabular w-full text-sm">
                    <thead className="bg-panel-2 text-[11px] uppercase text-muted"><tr><th className="px-3 py-2 text-left">Symbol</th><th className="px-3 py-2 text-left">Entered</th><th className="px-3 py-2 text-right">Shares</th><th className="px-3 py-2 text-right">Entry</th><th className="px-3 py-2 text-right">Last</th><th className="px-3 py-2 text-right">P&L</th><th className="px-3 py-2 text-right">Stop</th><th className="px-3 py-2 text-left">Status</th></tr></thead>
                    <tbody>{positions.map((p) => (
                      <tr key={p.symbol} className="border-t border-line/60">
                        <td className="px-3 py-1.5"><Link to="/stocks/$symbol" params={{ symbol: p.symbol }} search={keepAsOf} className="font-medium text-accent hover:underline">{p.symbol}</Link></td>
                        <td className="px-3 py-1.5 text-muted">{p.entry_date} · {p.days_held}d</td>
                        <td className="px-3 py-1.5 text-right">{num(p.shares)}</td>
                        <td className="px-3 py-1.5 text-right">{rupees(p.entry_price)}</td>
                        <td className="px-3 py-1.5 text-right">{rupees(p.last_close)}</td>
                        <td className={`px-3 py-1.5 text-right ${gainClass(p.unrealized_pnl)}`}>{pct(p.unrealized_ret, 1, true)} · {rupees(p.unrealized_pnl, 0)}</td>
                        <td className="px-3 py-1.5 text-right">{rupees(p.stop)}</td>
                        <td className="px-3 py-1.5">{p.pending_exit ? <Badge className="border-warn/60 bg-warn/10 text-warn">exit at open</Badge> : p.breakeven_active ? <Badge className="border-good/60 bg-good/10 text-good">breakeven</Badge> : <span className="text-muted">holding</span>}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
            </Card>
            <Card title="Closed trades" hint={`(latest ${trades.length})`}>
              {trades.length === 0 ? <p className="text-sm text-muted">None yet.</p> : (
                <ul className="max-h-96 divide-y divide-line overflow-y-auto pr-1 text-sm">{trades.map((t) => (
                  <li key={t.trade_id} className="flex items-center gap-3 py-1.5"><b className="w-24">{t.symbol}</b><span className="text-muted">{t.entry_date} → {t.exit_date} · {t.exit_reason}</span><span className={`tabular ml-auto ${gainClass(t.ret)}`}>{pct(t.ret, 1, true)}</span></li>
                ))}</ul>
              )}
            </Card>
          </div>
        )
      }}
    </QueryState>
  )
}
