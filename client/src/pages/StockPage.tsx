import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { send } from '../api/client'
import { q } from '../api/queries'
import type { PanelData } from '../api/types'
import { PriceChart } from '../components/PriceChart'
import { Badge, Card, QueryState, ScoreBar, Skeleton } from '../components/ui'
import { useAsOf } from '../lib/asof'
import { ENGINE_TONE, gainClass, STATUS_STYLE } from '../lib/colors'
import { crore, directionStyle, label, num, pct, rupees, safeUrl } from '../lib/format'

const LIFECYCLE_STYLE: Record<string, string> = {
  LEAD: 'border-good/60 bg-good/10 text-good',
  ADVANCE: 'border-good/40 bg-good/5 text-good',
  ACCUMULATION: 'border-info/60 bg-info/10 text-info',
  BASE: 'border-line bg-panel-2 text-muted',
  TOPPING: 'border-warn/60 bg-warn/10 text-warn',
  DECLINE: 'border-bad/60 bg-bad/10 text-bad',
  UNKNOWN: 'border-line bg-panel-2 text-muted',
}

function WatchButton({ symbol }: { symbol: string }) {
  const client = useQueryClient()
  const watch = useQuery(q.watchlist())
  const starred = watch.data?.data.some((w) => w.symbol === symbol) ?? false
  const toggle = useMutation({
    mutationFn: () => send(starred ? 'DELETE' : 'POST', `/api/watchlist/${symbol}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['watchlist'] }),
  })
  return (
    <button
      type="button"
      aria-pressed={starred}
      onClick={() => toggle.mutate()}
      disabled={toggle.isPending}
      className={`rounded border px-3 py-1 text-sm ${starred ? 'border-warn/60 bg-warn/10 text-warn' : 'border-line text-muted hover:text-text'}`}
    >
      {starred ? '★ Watching' : '☆ Watch'}
    </button>
  )
}

function Header({ panel, symbol }: { panel: PanelData; symbol: string }) {
  const { price, meta, lifecycle } = panel
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
      <h1 className="text-2xl font-bold">{symbol}</h1>
      <span className="tabular text-xl">{rupees(price.close)}</span>
      <span className={`tabular text-sm ${gainClass(price.ret_1d)}`}>{pct(price.ret_1d, 2, true)} 1d</span>
      <span className={`tabular text-sm ${gainClass(price.ret_21d)}`}>{pct(price.ret_21d, 1, true)} 1m</span>
      <span className="text-sm text-muted">
        {meta.company ?? ''}
        {meta.sector ? ` · ${meta.sector}` : ''}
        {meta.nse_industry ? ` › ${meta.nse_industry}` : ''}
      </span>
      {lifecycle.state && <Badge className={LIFECYCLE_STYLE[lifecycle.state]}>{lifecycle.state}</Badge>}
      <div className="ml-auto">
        <WatchButton symbol={symbol} />
      </div>
    </div>
  )
}

function SidePanel({ panel }: { panel: PanelData }) {
  const { lifecycle, gauges, relative_strength: rs, safety_stage1: safety, events } = panel
  return (
    <div className="space-y-4">
      <Card title="Momentum lifecycle">
        <div className="flex items-center gap-2">
          <Badge className={`px-2 py-1 text-xs ${LIFECYCLE_STYLE[lifecycle.state ?? 'UNKNOWN']}`}>{lifecycle.state ?? 'UNKNOWN'}</Badge>
          <span className="text-sm text-muted">
            {lifecycle.dwell_sessions}
            {lifecycle.dwell_is_lower_bound ? '+' : ''} sessions{lifecycle.since ? ` since ${lifecycle.since}` : ''}
          </span>
        </div>
        {lifecycle.previous_state && <p className="mt-2 text-xs text-muted">Previously {lifecycle.previous_state}.</p>}
        {lifecycle.state === 'UNKNOWN' && <p className="mt-2 text-xs text-muted">Needs 200+ sessions of history to classify.</p>}
      </Card>

      <Card title="Gauges">
        <div className="space-y-2">
          {gauges.map((g) => (
            <div key={g.name} className="flex items-center justify-between gap-3 text-sm">
              <span className="text-muted">{g.label}</span>
              <ScoreBar value={g.value} width="w-32" tone={ENGINE_TONE[g.name]} />
            </div>
          ))}
          <div className="flex justify-between border-t border-line pt-2 text-xs text-muted">
            <span>RS vs {rs.benchmark}</span>
            <span className="tabular">{rs.rs_500 == null ? '—' : num(rs.rs_500, 1)}</span>
          </div>
        </div>
      </Card>

      <Card title="Stage 1 — safety filter" right={<Badge className={STATUS_STYLE[safety.overall]}>{safety.overall}</Badge>}>
        <ul className="space-y-1.5 text-sm">
          {safety.checks.map((c) => (
            <li key={c.check} className="flex items-center justify-between gap-2">
              <span>
                {c.check}: <b className="tabular">{c.value == null ? '—' : num(c.value, c.value % 1 ? 1 : 0)}</b>{' '}
                <span className="text-xs text-muted">({c.threshold})</span>
              </span>
              <Badge className={STATUS_STYLE[c.status]}>{c.status}</Badge>
            </li>
          ))}
        </ul>
      </Card>

      {(events.news ?? []).length > 0 && (
        <Card title="News" hint="(extracted from announcements; context, not a signal)">
          <ul className="space-y-2.5 text-sm">
            {events.news?.map((n, i) => {
              const href = safeUrl(n.url)
              return (
                <li key={`${n.published_at}-${n.event_type}-${i}`}>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge className={directionStyle(n.direction)}>{n.direction}</Badge>
                    <span className="text-xs text-muted">{label(n.event_type)}</span>
                    <span className="tabular ml-auto text-xs text-muted">{n.published_at.slice(0, 10)}</span>
                  </div>
                  <p className="mt-1">{n.summary}</p>
                  <p className="mt-0.5 text-xs text-muted">
                    Confidence {pct(n.confidence, 0)}
                    {n.value_cr != null && ` · ${crore(n.value_cr)}`}
                    {href && (
                      <>
                        {' · '}
                        <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                          Source
                        </a>
                      </>
                    )}
                  </p>
                </li>
              )
            })}
          </ul>
        </Card>
      )}

      <Card title="Events">
        <div className="space-y-3 text-sm">
          <div>
            <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Circuit hits (last 10)</h4>
            {(events.circuit_hits ?? []).length === 0 ? (
              <p className="text-muted">None.</p>
            ) : (
              <p className="flex flex-wrap gap-1.5">
                {events.circuit_hits?.map((h) => (
                  <span key={h.date} className="rounded border border-line px-2 py-0.5 text-xs">
                    {h.date} {h.side === 'H' ? '▲ upper' : '▼ lower'}
                  </span>
                ))}
              </p>
            )}
          </div>
          <div>
            <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">Corporate actions</h4>
            {(events.corporate_actions ?? []).length === 0 ? (
              <p className="text-muted">None on file.</p>
            ) : (
              <ul className="space-y-1">
                {events.corporate_actions?.map((a) => (
                  <li key={`${a.ex_date}-${a.purpose}`} className="text-xs">
                    <span className="tabular text-muted">{a.ex_date}</span> {a.purpose}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Card>
    </div>
  )
}

export function StockPage({ symbol }: { symbol: string }) {
  const asOf = useAsOf()
  const sym = symbol.toUpperCase()
  const panel = useQuery({ ...q.panel(sym, asOf), placeholderData: keepPreviousData })
  const ohlcv = useQuery({ ...q.ohlcv(sym, asOf, 500), placeholderData: keepPreviousData })
  const features = useQuery({ ...q.features(sym, asOf, 500), placeholderData: keepPreviousData })

  return (
    <QueryState query={panel} empty={`No data for ${sym}. Check the symbol, or the date you are viewing.`} skeleton={<Skeleton className="h-96" />}>
      {(res) => (
        <div className="space-y-4">
          <Header panel={res.data} symbol={sym} />
          <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
            <Card title="Price" hint={`as of ${res.as_of}`}>
              <QueryState query={ohlcv} isEmpty={(r) => r.data.length === 0} skeleton={<Skeleton className="h-96" />}>
                {(o) => <PriceChart ohlcv={o.data} features={features.data?.data ?? []} />}
              </QueryState>
            </Card>
            <SidePanel panel={res.data} />
          </div>
        </div>
      )}
    </QueryState>
  )
}
