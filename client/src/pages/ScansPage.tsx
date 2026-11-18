import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from '@tanstack/react-router'
import type { ColumnDef } from '@tanstack/react-table'
import { useMemo } from 'react'
import { q } from '../api/queries'
import type { ScanRow } from '../api/types'
import { DataTable } from '../components/DataTable'
import { Badge, Card, QueryState, ScoreBar, Skeleton } from '../components/ui'
import { useAsOf } from '../lib/asof'

export function ScansPage({ scanId }: { scanId: string }) {
  const asOf = useAsOf()
  const navigate = useNavigate()
  const catalog = useQuery(q.scanCatalog())
  const results = useQuery({ ...q.scan(scanId, asOf, 100), placeholderData: keepPreviousData })
  const current = catalog.data?.data.scans.find((s) => s.id === scanId)

  const columns = useMemo<ColumnDef<ScanRow>[]>(
    () => [
      { accessorKey: 'rank', header: '#' },
      {
        accessorKey: 'symbol',
        header: 'Symbol',
        cell: (c) => (
          <Link
            to="/stocks/$symbol"
            params={{ symbol: c.getValue<string>() }}
            search={((prev: { as_of?: string }) => ({ as_of: prev.as_of })) as never}
            className="font-medium text-accent hover:underline"
            onClick={(e) => e.stopPropagation()}
          >
            {c.getValue<string>()}
          </Link>
        ),
      },
      { accessorKey: 'state', header: 'State', cell: (c) => <Badge className="border-line bg-panel-2 text-muted">{c.getValue<string>()}</Badge> },
      { accessorKey: 'score', header: 'Score', cell: (c) => <ScoreBar value={c.getValue<number>()} width="w-28" /> },
    ],
    [],
  )

  return (
    <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
      <Card title="Scans">
        <ul className="space-y-1">
          {(catalog.data?.data.scans ?? []).map((s) => (
            <li key={s.id}>
              <Link
                to="/scans/$scanId"
                params={{ scanId: s.id }}
                search={((prev: { as_of?: string }) => ({ as_of: prev.as_of })) as never}
                className="block rounded px-3 py-2 text-sm text-muted hover:bg-panel-2 hover:text-text"
                activeProps={{ className: 'bg-panel-2 text-text' }}
              >
                {s.label}
              </Link>
            </li>
          ))}
        </ul>
        {catalog.data && <p className="mt-3 text-xs text-muted">Universe: {catalog.data.data.universe}</p>}
      </Card>

      <Card title={current?.label ?? scanId} hint={results.data?.as_of ? `· ${results.data.data.length} candidates as of ${results.data.as_of}` : ''}>
        {current && <p className="mb-3 text-xs text-muted">Rule: <code className="text-text">{current.when}</code></p>}
        <QueryState query={results} isEmpty={(r) => r.data.length === 0} empty="No candidates on this date (scans need 200+ sessions of history)." skeleton={<Skeleton className="h-64" />}>
          {(res) => (
            <DataTable
              data={res.data}
              columns={columns}
              getRowId={(r) => r.symbol}
              maxHeight="70vh"
              onRowClick={(r) =>
                void navigate({ to: '/stocks/$symbol', params: { symbol: r.symbol }, search: ((prev: { as_of?: string }) => ({ as_of: prev.as_of })) as never })
              }
            />
          )}
        </QueryState>
      </Card>
    </div>
  )
}
