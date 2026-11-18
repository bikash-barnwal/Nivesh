import { keepPreviousData, useQuery } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'
import { useMemo } from 'react'
import { q } from '../api/queries'
import type { SectorRow, TrajectoryCard } from '../api/types'
import { DataTable } from '../components/DataTable'
import { Badge, Card, QueryState, ScoreBar, Skeleton } from '../components/ui'
import { useAsOf } from '../lib/asof'
import { gainClass, SEVERITY_STYLE, TREND_STYLE, VERDICT_STYLE } from '../lib/colors'
import { label, num, pct, signed } from '../lib/format'

const arrow = (v: number | null) => (v == null || v === 0 ? '·' : v > 0 ? `▲${v}` : `▼${Math.abs(v)}`)

function TrajectoryList({ title, cards, tone }: { title: string; cards: TrajectoryCard[]; tone: 'good' | 'bad' }) {
  return (
    <div>
      <h3 className={`mb-2 text-[11px] font-semibold uppercase tracking-wide ${tone === 'good' ? 'text-good' : 'text-bad'}`}>{title}</h3>
      {cards.length === 0 && <p className="text-sm text-muted">None.</p>}
      <div className="space-y-2">
        {cards.map((c) => (
          <div key={c.sector} className="rounded-lg border border-line bg-panel-2/40 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold">{c.sector}</span>
              <Badge className={TREND_STYLE[c.rs_trend]}>{c.rs_trend}</Badge>
            </div>
            <div className="tabular mt-1 text-sm">
              {c.rank_path.map((r, i) => (
                <span key={i}>
                  {i > 0 && <span className="mx-1 text-muted">→</span>}
                  {r == null ? '—' : `#${r}`}
                </span>
              ))}
              <span className={`ml-3 text-xs ${c.ranks_per_quarter >= 0 ? 'text-good' : 'text-bad'}`}>
                {signed(c.ranks_per_quarter, 0)} ranks / quarter
              </span>
            </div>
            <div className="mt-1 text-xs text-muted">
              participation {num(c.participation, 0)} · flow {num(c.flow, 0)} · exhaustion {num(c.exhaustion, 0)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function SectorsPage() {
  const asOf = useAsOf()
  const sectors = useQuery({ ...q.sectors(asOf), placeholderData: keepPreviousData })
  const events = useQuery({ ...q.sectorEvents(asOf), placeholderData: keepPreviousData })

  const columns = useMemo<ColumnDef<SectorRow>[]>(
    () => [
      { accessorKey: 'rank', header: '#', cell: (c) => c.getValue<number | null>() ?? '—', sortUndefined: 'last' },
      {
        accessorKey: 'sector',
        header: 'Group',
        cell: (c) => (
          <span>
            <span className="font-medium">{c.getValue<string>()}</span>{' '}
            <span className="text-xs text-muted">{c.row.original.n_stocks}</span>
          </span>
        ),
      },
      {
        accessorKey: 'verdict',
        header: 'Verdict',
        cell: (c) => {
          const v = c.getValue<string | null>()
          return v ? (
            <span className="flex items-center gap-1.5">
              <Badge className={VERDICT_STYLE[v]}>{v}</Badge>
              <span className="text-xs text-muted">{c.row.original.days_in_verdict}d</span>
            </span>
          ) : (
            <span className="text-muted">—</span>
          )
        },
      },
      ...(['strength', 'rs', 'participation', 'flow', 'leadership', 'exhaustion'] as const).map<ColumnDef<SectorRow>>((key) => ({
        accessorKey: key,
        header: label(key),
        cell: (c) => <ScoreBar value={c.getValue<number | null>()} width="w-14" tone={key === 'exhaustion' ? 'bad-high' : 'good-high'} />,
      })),
      { accessorKey: 'rank_1q', header: 'Rank 1q', cell: (c) => c.getValue<number | null>() ?? '—' },
      ...(['d1q', 'd1m', 'd1w'] as const).map<ColumnDef<SectorRow>>((key) => ({
        accessorKey: key,
        header: `Δ${key.slice(1)}`,
        cell: (c) => <span className={gainClass(c.getValue<number | null>())}>{arrow(c.getValue<number | null>())}</span>,
      })),
      {
        accessorKey: 'rs_trend',
        header: 'RS trend',
        cell: (c) => {
          const v = c.getValue<string | null>()
          return v ? <Badge className={TREND_STYLE[v]}>{v}</Badge> : '—'
        },
      },
      { accessorKey: 'excess_1m', header: 'Exc 1m', cell: (c) => <span className={gainClass(c.getValue<number | null>())}>{pct(c.getValue<number | null>(), 1, true)}</span> },
      { accessorKey: 'excess_3m', header: 'Exc 3m', cell: (c) => <span className={gainClass(c.getValue<number | null>())}>{pct(c.getValue<number | null>(), 1, true)}</span> },
      { accessorKey: 'turnover_share', header: 'TO share', cell: (c) => pct(c.getValue<number | null>(), 1) },
      { accessorKey: 'net_flow_cr', header: 'Net flow ₹Cr', cell: (c) => <span className={gainClass(c.getValue<number | null>())}>{num(c.getValue<number | null>(), 0)}</span> },
    ],
    [],
  )

  return (
    <div className="space-y-4">
      <QueryState query={sectors} isEmpty={(r) => r.data.length === 0} empty="Sector intelligence needs 63+ sessions and a sector map." skeleton={<Skeleton className="h-40" />}>
        {(res) => (
          <>
            <Card
              title="Sector & industry intelligence"
              hint={`as of ${res.as_of} · vs ${'Nifty 500'}`}
            >
              <div className="mb-3 flex flex-wrap gap-2 text-xs">
                {res.header.risk_posture && (
                  <Badge className={res.header.risk_posture === 'risk-on' ? 'border-good/60 bg-good/10 text-good' : 'border-bad/60 bg-bad/10 text-bad'}>
                    Risk posture: {res.header.risk_posture} ({signed(res.header.cyclical_minus_defensive_pp, 1)}pp)
                  </Badge>
                )}
                {res.header.dispersion_percentile != null && (
                  <Badge className="border-line bg-panel-2 text-muted">Dispersion: {num(res.header.dispersion_percentile, 0)}th pctl</Badge>
                )}
                {res.header.leader_persistence_1m != null && (
                  <Badge className="border-line bg-panel-2 text-muted">Leader persistence 1m: {pct(res.header.leader_persistence_1m, 0)}</Badge>
                )}
              </div>
              <p className="border-l-2 border-line pl-3 text-sm leading-relaxed">{res.header.narrative}</p>
            </Card>

            <Card title="RS trajectory" hint="(rank momentum over the last quarter)">
              <div className="grid gap-6 lg:grid-cols-2">
                <TrajectoryList title="▲ RS improving — momentum catching up on rank" cards={res.trajectories.improving} tone="good" />
                <TrajectoryList title="▼ RS deteriorating — rotation leaving" cards={res.trajectories.deteriorating} tone="bad" />
              </div>
            </Card>

            <Card title="Group leaderboard" hint="(click a header to sort)">
              <DataTable
                data={res.data}
                columns={columns}
                getRowId={(r) => r.sector}
                initialSort={[{ id: 'rank', desc: false }]}
                rowClass={(r) => (r.sector === 'Unclassified' ? 'italic text-muted' : '')}
              />
              <p className="mt-2 text-xs text-muted">
                Strength = 40% RS + 30% participation + 30% flow. “Unclassified” counts liquid stocks without a mapped sector; they are shown but not ranked.
              </p>
            </Card>
          </>
        )}
      </QueryState>

      <Card title="Active group events" hint="(last 10 sessions)">
        <QueryState query={events} isEmpty={(r) => r.data.length === 0} empty="No group events in this window." skeleton={<Skeleton className="h-24" />}>
          {(res) => (
            <ul className="max-h-[28rem] space-y-2 overflow-y-auto pr-1">
              {res.data.map((e) => (
                <li key={`${e.date}-${e.sector}-${e.event}`} className={`rounded-lg border px-3 py-2 ${SEVERITY_STYLE[e.severity]?.split(' ')[0] ?? 'border-line'} bg-panel-2/40`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className={SEVERITY_STYLE[e.severity]}>{label(e.event)}</Badge>
                    <span className="font-semibold">{e.sector}</span>
                    <span className="ml-auto text-xs text-muted">{e.date}</span>
                  </div>
                  <p className="mt-1 text-sm text-muted">{e.description}</p>
                  <p className="mt-1 text-xs text-muted">
                    Historically ({e.historical.occurrences ?? 0} occurrences) median excess{' '}
                    {(['1w', '1m', '3m'] as const).map((h) => (
                      <span key={h} className="mr-3">
                        +{h} <span className={gainClass(e.historical[`med_${h}`] ?? null)}>{pct(e.historical[`med_${h}`] ?? null, 1, true)}</span> (hit {pct(e.historical[`hit_${h}`] ?? null, 0)})
                      </span>
                    ))}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </QueryState>
      </Card>
    </div>
  )
}
