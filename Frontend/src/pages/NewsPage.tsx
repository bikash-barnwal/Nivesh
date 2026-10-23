import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { ColumnDef } from '@tanstack/react-table'
import { useMemo } from 'react'
import { news } from '../api/queries'
import type { NewsStudyRow } from '../api/types'
import { DataTable } from '../components/DataTable'
import { Badge, Card, QueryState, Skeleton } from '../components/ui'
import { useAsOf } from '../lib/asof'
import { gainClass } from '../lib/colors'
import { crore, directionStyle, label, num, pct, safeUrl } from '../lib/format'

const EMPTY = 'No news events yet. Run `nivesh news-ingest` then `nivesh news-extract`.'

const gainCol = (accessorKey: keyof NewsStudyRow, header: string): ColumnDef<NewsStudyRow> => ({
  accessorKey,
  header,
  cell: (c) => <span className={`tabular ${gainClass(c.getValue<number | null>())}`}>{pct(c.getValue<number | null>(), 2, true)}</span>,
})

const plainCol = (accessorKey: keyof NewsStudyRow, header: string, format: (x: number | null) => string): ColumnDef<NewsStudyRow> => ({
  accessorKey,
  header,
  cell: (c) => <span className="tabular">{format(c.getValue<number | null>())}</span>,
})

function Status() {
  const status = useQuery(news.status())
  return (
    <QueryState query={status} empty={EMPTY} skeleton={<Skeleton className="h-5" />}>
      {(res) => {
        const extracted = Object.entries(res.data.extracted).map(([k, v]) => `${num(v)} ${k}`)
        const events = Object.values(res.data.events_by_type).reduce((a, b) => a + b, 0)
        return (
          <p className="text-sm text-muted" role="status">
            {num(res.data.items)} items ingested · {extracted.join(', ') || 'none extracted'} · {num(events)} events · data through{' '}
            {res.data_through ?? '—'}
          </p>
        )
      }}
    </QueryState>
  )
}

function Study() {
  const study = useQuery(news.study())
  const columns = useMemo<ColumnDef<NewsStudyRow>[]>(
    () => [
      { accessorKey: 'event_type', header: 'Event', cell: (c) => label(c.getValue<string>()) },
      { accessorKey: 'direction', header: 'Direction', cell: (c) => <Badge className={directionStyle(c.getValue<string>())}>{c.getValue<string>()}</Badge> },
      plainCol('n', 'n', (x) => num(x)),
      gainCol('mean_excess_1d', 'Excess 1d'),
      gainCol('mean_excess_5d', 'Excess 5d'),
      gainCol('mean_excess_20d', 'Excess 20d'),
      plainCol('hit_rate_5d', 'Hit 5d', (x) => pct(x, 0)),
      plainCol('hit_rate_20d', 'Hit 20d', (x) => pct(x, 0)),
      plainCol('t_stat_5d', 't 5d', (x) => num(x, 2)),
      plainCol('t_stat_20d', 't 20d', (x) => num(x, 2)),
    ],
    [],
  )
  return (
    <Card title="Event study">
      <p className="mb-3 text-sm text-muted">
        Average return in excess of the liquid market after each kind of news, entering at the next open. Small n is weak evidence, and this describes the past rather than predicting anything.
      </p>
      <QueryState query={study} isEmpty={(r) => r.data.length === 0} empty={EMPTY} skeleton={<Skeleton className="h-40" />}>
        {(res) => <DataTable data={res.data} columns={columns} initialSort={[{ id: 'n', desc: true }]} getRowId={(r) => `${r.event_type}-${r.direction}`} />}
      </QueryState>
    </Card>
  )
}

function Events() {
  const asOf = useAsOf()
  const events = useQuery({ ...news.events(asOf), placeholderData: keepPreviousData })
  return (
    <Card title="Recent events" hint={asOf ? `(as of ${asOf})` : undefined}>
      <QueryState query={events} isEmpty={(r) => r.data.length === 0} empty={EMPTY} skeleton={<Skeleton className="h-48" />}>
        {(res) => (
          <ul className="divide-y divide-line">
            {res.data.map((e) => {
              const href = safeUrl(e.url)
              return (
                <li key={e.source_id} className="space-y-1 py-2.5 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className={directionStyle(e.direction)}>{e.direction}</Badge>
                    <Link
                      to="/stocks/$symbol"
                      params={{ symbol: e.symbol }}
                      search={((prev: { as_of?: string }) => ({ as_of: prev.as_of })) as never}
                      className="font-medium text-accent hover:underline"
                    >
                      {e.symbol}
                    </Link>
                    <span className="text-muted">{label(e.event_type)}</span>
                    <span className="tabular ml-auto text-xs text-muted">{e.event_date}</span>
                  </div>
                  <p>{e.summary}</p>
                  <p className="text-xs text-muted">
                    Confidence {pct(e.confidence, 0)}
                    {e.value_cr != null && ` · Amount ${crore(e.value_cr)}`}
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
        )}
      </QueryState>
    </Card>
  )
}

export function NewsPage() {
  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">News</h1>
        <Status />
      </div>
      <Study />
      <Events />
    </div>
  )
}
