import { keepPreviousData, useQuery } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useMemo, useRef, useState } from 'react'
import { q } from '../api/queries'
import type { AnalogueRow, EngineRow, EventRow, EventsResponse, RegimeRow } from '../api/types'
import { DataTable } from '../components/DataTable'
import { Drawer } from '../components/Drawer'
import { RegimeStrip } from '../components/RegimeStrip'
import { Badge, Card, QueryState, ScoreBar, Skeleton, Spark } from '../components/ui'
import { useAsOf, useSetAsOf } from '../lib/asof'
import { ENGINE_TONE, gainClass, POSTURE_STYLE, REGIME_COLORS, scoreColor, SEVERITY_STYLE } from '../lib/colors'
import { label, num, pct, signed } from '../lib/format'

const ENGINE_INFO: Record<string, { title: string; what: string }> = {
  buying_pressure: { title: 'Buying Pressure', what: 'How many liquid stocks are rising over 4 sessions, blended with the share of volume traded on up days.' },
  selling_pressure: { title: 'Selling Pressure', what: 'How many liquid stocks are falling over 4 sessions, blended with the share of stocks breaking below their 20-day average.' },
  leadership: { title: 'Leadership', what: 'New 52-week highs minus new 52-week lows, as a share of the universe.' },
  liquidity: { title: 'Liquidity & Money Flow', what: 'Turnover over the last 5 sessions relative to the 50-session average.' },
  momentum_quality: { title: 'Momentum Quality', what: 'Share of stocks in aligned uptrends (close > SMA20 > SMA50 > SMA200).' },
  participation: { title: 'Participation', what: 'Share of liquid stocks trading above their 50-day average.' },
  volatility: { title: 'Volatility', what: 'Median 14-session ATR as a share of price across the universe.' },
  risk_exhaustion: { title: 'Risk & Exhaustion', what: 'Share of stocks with RSI14 above 70, blended with the share above their 20-day average.' },
  structural_breadth: { title: 'Structural Breadth', what: 'Share of liquid stocks trading above their 200-day average.' },
}

function Ribbon({ asOf }: { asOf?: string }) {
  const overview = useQuery({ ...q.overview(asOf), placeholderData: keepPreviousData })
  return (
    <QueryState query={overview} isEmpty={(r) => !r.data} empty="Regime data needs 200+ sessions of history. Run the backfill to see it." skeleton={<Skeleton className="h-28" />}>
      {(res) => {
        const { regime, narrative, active_events: active } = res.data!
        return (
          <Card>
            <div className="flex flex-wrap items-center gap-3">
              <Badge className={`px-2.5 py-1 text-xs ${POSTURE_STYLE[regime.posture] ?? ''}`}>{regime.posture}</Badge>
              <span
                className="rounded-full border px-3 py-0.5 text-sm font-semibold"
                style={{ borderColor: REGIME_COLORS[regime.regime], color: REGIME_COLORS[regime.regime] }}
              >
                {regime.regime}
              </span>
              <span className="text-xs text-muted">
                stable in <b className="text-text">{num(regime.confidence, 0)}%</b> of recent sessions · {regime.sessions_active} sessions active · as of{' '}
                <b className="text-text">{res.as_of}</b>
              </span>
            </div>
            <p className="mt-3 border-l-2 border-line pl-3 text-sm leading-relaxed">{narrative}</p>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              {[
                regime.idx_drawdown != null && `Index ${pct(regime.idx_drawdown, 1)} from 252-session high`,
                regime.pct_above_sma200 != null && `${pct(regime.pct_above_sma200, 0)} of stocks above 200-DMA`,
                regime.pct_above_sma50 != null && `${pct(regime.pct_above_sma50, 0)} above 50-DMA`,
                `${num(regime.n_stocks)} stocks in breadth universe`,
              ]
                .filter(Boolean)
                .map((text) => (
                  <span key={String(text)} className="rounded-full border border-line bg-panel-2 px-3 py-1 text-muted">
                    {text}
                  </span>
                ))}
              {active.map((e) => (
                <Badge key={e.event} className={SEVERITY_STYLE[e.severity]}>
                  {e.severity} · {label(e.event)}
                </Badge>
              ))}
            </div>
          </Card>
        )
      }}
    </QueryState>
  )
}

function RegimeHistory({ asOf }: { asOf?: string }) {
  const setAsOf = useSetAsOf()
  const history = useQuery({ ...q.regimeHistory(asOf, 150), placeholderData: keepPreviousData })
  return (
    <Card title="Regime history" hint="(150 sessions · click a cell to time-travel)">
      <QueryState query={history} isEmpty={(r) => r.data.length === 0} skeleton={<Skeleton className="h-16" />}>
        {(res) => <RegimeStrip rows={res.data as RegimeRow[]} selected={asOf} onPick={setAsOf} />}
      </QueryState>
    </Card>
  )
}

function EngineCard({ row, onOpen }: { row: EngineRow; onOpen: () => void }) {
  const info = ENGINE_INFO[row.engine]
  const tone = ENGINE_TONE[row.engine] ?? 'good-high'
  const color = scoreColor(row.value, tone)
  return (
    <button type="button" onClick={onOpen} className="rounded-lg border border-line bg-panel p-3 text-left transition-colors hover:border-accent/60">
      <div className="flex items-start justify-between gap-2">
        <span className="text-[13px] font-semibold">{info?.title ?? label(row.engine)}</span>
        <span className="tabular text-xl font-bold" style={{ color }}>
          {row.value == null ? '—' : row.value.toFixed(0)}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between text-xs text-muted">
        <span>
          {row.label ?? 'Warming up'}
          {row.delta_10 != null && (
            <span className={`ml-2 tabular ${gainClass(row.delta_10)}`}>
              {row.delta_10 > 0 ? '▲' : row.delta_10 < 0 ? '▼' : '='} {signed(row.delta_10, 0)}
            </span>
          )}
        </span>
        <Spark values={row.spark ?? []} color={color} width={90} height={24} />
      </div>
      <div className="mt-2">
        <ScoreBar value={row.value} width="w-full" tone={tone} />
      </div>
    </button>
  )
}

function Engines({ asOf }: { asOf?: string }) {
  const engines = useQuery({ ...q.engines(asOf), placeholderData: keepPreviousData })
  const catalog = useQuery(q.engineCatalog())
  const [open, setOpen] = useState<EngineRow | null>(null)
  return (
    <Card title="Intelligence engines" hint="(click a card for its evidence)">
      <QueryState query={engines} isEmpty={(r) => r.data.length === 0} empty="Engines need 120+ sessions of breadth history." skeleton={<Skeleton className="h-40" />}>
        {(res) => (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {res.data.map((row) => (
              <EngineCard key={row.engine} row={row} onOpen={() => setOpen(row)} />
            ))}
          </div>
        )}
      </QueryState>
      <Drawer open={!!open} title={open ? (ENGINE_INFO[open.engine]?.title ?? label(open.engine)) : ''} onClose={() => setOpen(null)}>
        {open && (
          <div className="space-y-4 text-sm">
            <p className="text-muted">{ENGINE_INFO[open.engine]?.what}</p>
            <dl className="grid grid-cols-2 gap-2 rounded border border-line bg-panel-2 p-3 text-xs">
              <dt className="text-muted">Value (percentile)</dt>
              <dd className="tabular text-right">{open.value?.toFixed(1) ?? '—'}</dd>
              <dt className="text-muted">Label</dt>
              <dd className="text-right">{open.label ?? '—'}</dd>
              <dt className="text-muted">10-session change</dt>
              <dd className="tabular text-right">{signed(open.delta_10, 1)}</dd>
              <dt className="text-muted">As of</dt>
              <dd className="text-right">{open.date}</dd>
            </dl>
            <div>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Last {open.spark?.length ?? 0} sessions</h3>
              <Spark values={open.spark ?? []} color={scoreColor(open.value, ENGINE_TONE[open.engine])} width={380} height={80} />
            </div>
            <div>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">How it is computed</h3>
              <ul className="list-inside list-disc space-y-1 text-muted">
                <li>
                  Inputs: <code className="text-text">{catalog.data?.data.engines[open.engine]?.join(', ') ?? '…'}</code>
                </li>
                <li>
                  Percentile rank over the last {catalog.data?.data.window_sessions ?? '…'} sessions (shown only after{' '}
                  {catalog.data?.data.minimum_sessions ?? '…'}).
                </li>
                <li>Definitions live in <code className="text-text">context/engines.yaml</code>.</li>
              </ul>
            </div>
          </div>
        )}
      </Drawer>
    </Card>
  )
}

function OutcomeTable({ event, baseline }: { event: EventRow; baseline: EventsResponse['baseline'] }) {
  const rows = (['1w', '1m', '3m'] as const).map((h) => ({
    h,
    median: event.outcomes[`med_${h}`] ?? null,
    hit: event.outcomes[`hit_${h}`] ?? null,
    n: event.outcomes[`n_${h}`] ?? 0,
    baseMedian: baseline[`med_${h}`] ?? null,
    baseHit: baseline[`hit_${h}`] ?? null,
  }))
  return (
    <table className="tabular w-full text-xs">
      <thead>
        <tr className="text-left text-[11px] uppercase tracking-wide text-muted">
          <th className="py-1 font-semibold">Forward</th>
          <th className="py-1 text-right font-semibold">Median</th>
          <th className="py-1 text-right font-semibold">Hit rate</th>
          <th className="py-1 text-right font-semibold">n</th>
          <th className="py-1 text-right font-semibold">All days</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.h} className="border-t border-line/60">
            <td className="py-1">+{r.h}</td>
            <td className={`py-1 text-right ${gainClass(r.median)}`}>{pct(r.median, 2, true)}</td>
            <td className="py-1 text-right">{pct(r.hit, 0)}</td>
            <td className="py-1 text-right text-muted">{r.n}</td>
            <td className="py-1 text-right text-muted">
              {pct(r.baseMedian, 2, true)} · {pct(r.baseHit, 0)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Events({ asOf }: { asOf?: string }) {
  const events = useQuery({ ...q.events(asOf), placeholderData: keepPreviousData })
  return (
    <Card title="Active intelligence events">
      <QueryState query={events} isEmpty={(r) => r.data.length === 0} empty="No events are firing on this date." skeleton={<Skeleton className="h-32" />}>
        {(res) => (
          <div className="space-y-2">
            {res.data.map((e) => (
              <details key={e.event} className={`rounded-lg border ${SEVERITY_STYLE[e.severity]?.split(' ')[0] ?? 'border-line'} bg-panel-2/40`} open={e.severity === 'CRITICAL'}>
                <summary className="flex cursor-pointer flex-wrap items-center gap-3 px-3 py-2">
                  <Badge className={SEVERITY_STYLE[e.severity]}>{e.severity}</Badge>
                  <span className="font-semibold">{label(e.event)}</span>
                  <span className="ml-auto text-xs text-muted">
                    {e.streak}d streak · {e.horizon} · confidence {num(e.confidence, 0)}%
                  </span>
                </summary>
                <div className="grid gap-4 border-t border-line/60 px-3 py-3 lg:grid-cols-2">
                  <div className="space-y-3 text-sm">
                    <p>{e.description}</p>
                    <div>
                      <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Suggested actions</h4>
                      <dl className="space-y-1 text-[13px]">
                        {(['short', 'swing', 'positional'] as const).map(
                          (h) =>
                            e.actions[h] && (
                              <div key={h}>
                                <dt className="inline font-semibold capitalize">{h}: </dt>
                                <dd className="inline text-muted">{e.actions[h]}</dd>
                              </div>
                            ),
                        )}
                      </dl>
                    </div>
                  </div>
                  <div>
                    <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">
                      Historical outcomes · benchmark return after {e.outcomes.episodes ?? 0} prior episodes
                    </h4>
                    <OutcomeTable event={e} baseline={res.baseline ?? {}} />
                    {e.recent_occurrences.length > 0 && (
                      <p className="mt-2 flex flex-wrap gap-2 text-xs text-muted">
                        Recent:
                        {e.recent_occurrences.map((o) => (
                          <span key={o.date} className="rounded-full border border-line px-2 py-0.5">
                            {o.date} → <span className={gainClass(o.fwd_1m)}>{pct(o.fwd_1m, 1, true)}</span> in 1m
                          </span>
                        ))}
                      </p>
                    )}
                  </div>
                </div>
              </details>
            ))}
          </div>
        )}
      </QueryState>
    </Card>
  )
}

function EventLog({ asOf }: { asOf?: string }) {
  const log = useQuery({ ...q.eventLog(asOf), placeholderData: keepPreviousData })
  const parent = useRef<HTMLDivElement>(null)
  const rows = log.data?.data ?? []
  const virtual = useVirtualizer({ count: rows.length, getScrollElement: () => parent.current, estimateSize: () => 32, overscan: 8 })
  return (
    <details className="rounded-lg border border-line bg-panel">
      <summary className="cursor-pointer px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-muted">Event log — last 30 sessions</summary>
      <div ref={parent} className="max-h-72 overflow-auto border-t border-line">
        {rows.length === 0 && <p className="p-4 text-sm text-muted">No events in this window.</p>}
        <div style={{ height: virtual.getTotalSize(), position: 'relative' }}>
          {virtual.getVirtualItems().map((item) => {
            const e = rows[item.index]!
            return (
              <div key={`${e.date}-${e.event}`} className="absolute inset-x-0 flex items-center gap-3 border-b border-line/50 px-4 text-[13px]" style={{ height: item.size, transform: `translateY(${item.start}px)` }}>
                <span className="tabular w-24 text-muted">{e.date}</span>
                <Badge className={SEVERITY_STYLE[e.severity]}>{e.severity}</Badge>
                <span>{label(e.event)}</span>
                <span className="ml-auto text-xs text-muted">{e.streak}d</span>
              </div>
            )
          })}
        </div>
      </div>
    </details>
  )
}

function Analogues({ asOf }: { asOf?: string }) {
  const setAsOf = useSetAsOf()
  const analogues = useQuery({ ...q.analogues(asOf), placeholderData: keepPreviousData })
  const columns = useMemo<ColumnDef<AnalogueRow>[]>(
    () => [
      { accessorKey: 'analogue_date', header: 'Date' },
      { accessorKey: 'similarity_pct', header: 'Similarity', cell: (c) => `${num(c.getValue<number>(), 0)}%` },
      {
        accessorKey: 'regime_then',
        header: 'Regime then',
        cell: (c) => {
          const v = c.getValue<string | null>()
          return v ? <span style={{ color: REGIME_COLORS[v] }}>{v}</span> : '—'
        },
      },
      ...(['fwd_1w', 'fwd_1m', 'fwd_3m'] as const).map<ColumnDef<AnalogueRow>>((key) => ({
        accessorKey: key,
        header: `+${key.slice(4)} after`,
        cell: (c) => <span className={gainClass(c.getValue<number | null>())}>{pct(c.getValue<number | null>(), 1, true)}</span>,
      })),
    ],
    [],
  )
  return (
    <Card title="Similar past markets" hint="(closest engine-profile matches · click a row to rewind)">
      <QueryState query={analogues} isEmpty={(r) => r.data.length === 0} empty="Analogues need engine history (120+ sessions)." skeleton={<Skeleton className="h-40" />}>
        {(res) => (
          <div className="space-y-3">
            <p className="text-sm text-muted">
              Of the {res.summary.matches} closest analogues, the index was higher one month later{' '}
              <b className="text-text">{pct(res.summary.share_positive_1m, 0)}</b> of the time (median {pct(res.summary.median_1m, 1, true)}) and three months later{' '}
              <b className="text-text">{pct(res.summary.share_positive_3m, 0)}</b> (median {pct(res.summary.median_3m, 1, true)}). {res.summary.note}
            </p>
            <DataTable data={res.data} columns={columns} onRowClick={(r) => setAsOf(r.analogue_date)} getRowId={(r) => r.analogue_date} />
          </div>
        )}
      </QueryState>
    </Card>
  )
}

export function MarketPage() {
  const asOf = useAsOf()
  return (
    <div className="space-y-4">
      <Ribbon asOf={asOf} />
      <RegimeHistory asOf={asOf} />
      <Engines asOf={asOf} />
      <Events asOf={asOf} />
      <EventLog asOf={asOf} />
      <Analogues asOf={asOf} />
    </div>
  )
}
