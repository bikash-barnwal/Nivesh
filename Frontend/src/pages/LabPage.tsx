import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { ColumnDef } from '@tanstack/react-table'
import { useEffect, useMemo, useState } from 'react'
import { type Envelope, post } from '../api/client'
import { lab } from '../api/queries'
import type { JobStatus, StrategyRow } from '../api/types'
import { DataTable } from '../components/DataTable'
import { Badge, Card, QueryState, Skeleton } from '../components/ui'
import { gainClass, STATUS_STYLE } from '../lib/colors'
import { num, pct } from '../lib/format'

const STATUS_BADGE: Record<string, string> = {
  live: 'border-good/60 bg-good/10 text-good',
  paper: 'border-info/60 bg-info/10 text-info',
  research: 'border-line bg-panel-2 text-muted',
  retired: 'border-bad/40 bg-bad/5 text-bad',
}

/** Polls one backtest job and refreshes the leaderboard when it lands. */
function JobWatcher({ jobId, onDone }: { jobId: string; onDone: () => void }) {
  const job = useQuery(lab.job(jobId))
  const status = job.data?.data.status
  useEffect(() => {
    if (status && status !== 'running') onDone()
  }, [status, onDone])
  if (status === 'failed') return <span className="text-xs text-bad" title={job.data?.data.error ?? ''}>Failed: {job.data?.data.error}</span>
  if (status === 'done') return <span className="text-xs text-good">Done</span>
  return <span className="text-xs text-muted">Running…</span>
}

export function LabPage() {
  const client = useQueryClient()
  const strategies = useQuery(lab.strategies())
  const [jobs, setJobs] = useState<Record<string, string>>({})
  const start = useMutation({
    mutationFn: (strategyId: string) => post<Envelope<JobStatus>>('/api/backtests', { strategy_id: strategyId, gates: true }),
    onSuccess: (res, strategyId) => setJobs((j) => ({ ...j, [strategyId]: res.data.job_id })),
  })

  const columns = useMemo<ColumnDef<StrategyRow>[]>(
    () => [
      {
        id: 'strategy',
        header: 'Strategy',
        accessorFn: (r) => r.label,
        cell: (c) => (
          <div>
            <div className="flex items-center gap-2">
              <span className="font-medium">{c.row.original.label}</span>
              <Badge className={STATUS_BADGE[c.row.original.status]}>{c.row.original.status}</Badge>
            </div>
            <div className="max-w-md truncate text-xs text-muted" title={c.row.original.entry}>{c.row.original.entry}</div>
          </div>
        ),
      },
      { id: 'cagr', header: 'CAGR', accessorFn: (r) => r.latest_run?.cagr ?? null, cell: (c) => <span className={gainClass(c.getValue<number | null>())}>{pct(c.getValue<number | null>(), 1, true)}</span> },
      { id: 'dd', header: 'Max DD', accessorFn: (r) => r.latest_run?.max_drawdown ?? null, cell: (c) => <span className="text-bad">{pct(c.getValue<number | null>(), 1)}</span> },
      { id: 'calmar', header: 'Calmar', accessorFn: (r) => r.latest_run?.calmar ?? null, cell: (c) => num(c.getValue<number | null>(), 2) },
      { id: 'sharpe', header: 'Sharpe', accessorFn: (r) => r.latest_run?.sharpe ?? null, cell: (c) => num(c.getValue<number | null>(), 2) },
      { id: 'trades', header: 'Trades', accessorFn: (r) => r.latest_run?.trades ?? null, cell: (c) => num(c.getValue<number | null>()) },
      { id: 'win', header: 'Win %', accessorFn: (r) => r.latest_run?.win_rate ?? null, cell: (c) => pct(c.getValue<number | null>(), 1) },
      {
        id: 'gates',
        header: 'Gates',
        accessorFn: (r) => r.latest_run?.gates_passed ?? null,
        cell: (c) => {
          const run = c.row.original.latest_run
          if (!run || run.gates_total == null) return <span className="text-muted">—</span>
          return (
            <span className="flex items-center gap-1.5">
              <Badge className={STATUS_STYLE[run.gates_all_passed ? 'PASS' : 'FAIL']}>{run.gates_passed}/{run.gates_total}</Badge>
              {run.review_required && <Badge className={STATUS_STYLE.INCOMPLETE} title="Results look too good: check for overfitting or bias">Review</Badge>}
            </span>
          )
        },
      },
      { id: 'when', header: 'Window', accessorFn: (r) => r.latest_run?.start ?? '', cell: (c) => { const run = c.row.original.latest_run; return run ? <span className="text-xs text-muted">{run.start} → {run.end}</span> : <span className="text-muted">never run</span> } },
      {
        id: 'actions',
        header: '',
        enableSorting: false,
        cell: (c) => {
          const row = c.row.original
          const job = jobs[row.id]
          return (
            <span className="flex items-center gap-2">
              <button
                type="button"
                disabled={start.isPending && start.variables === row.id}
                onClick={(e) => { e.stopPropagation(); start.mutate(row.id) }}
                className="rounded border border-line px-2 py-1 text-xs hover:bg-panel-2 disabled:opacity-50"
              >
                {row.latest_run ? 'Re-run' : 'Run backtest'}
              </button>
              {row.latest_run && (
                <Link to="/runs/$runId" params={{ runId: row.latest_run.run_id }} onClick={(e) => e.stopPropagation()} className="text-xs text-accent hover:underline">
                  Open
                </Link>
              )}
              {job && <JobWatcher jobId={job} onDone={() => { void client.invalidateQueries({ queryKey: ['lab'] }) }} />}
            </span>
          )
        },
      },
    ],
    [client, jobs, start],
  )

  return (
    <div className="space-y-4">
      <Card title="Strategy lab" hint="(YAML-defined strategies · point-in-time data · costs and slippage included)">
        <p className="mb-3 text-sm text-muted">
          Strategies live in <code className="text-text">Backend/context/strategies/</code>. A backtest enters on the next open after a signal, applies stops, gap
          risk, brokerage, STT and slippage, and is then put through anti-overfitting gates (out-of-sample, parameter sensitivity, cost stress, regime spread).
          Passing the gates is necessary, not sufficient.
        </p>
        {start.isError && <p className="mb-3 text-sm text-bad">{(start.error as Error).message}</p>}
        <QueryState query={strategies} isEmpty={(r) => r.data.length === 0} empty="No strategies found." skeleton={<Skeleton className="h-40" />}>
          {(res) => <DataTable data={res.data} columns={columns} getRowId={(r) => r.id} initialSort={[{ id: 'calmar', desc: true }]} />}
        </QueryState>
      </Card>
    </div>
  )
}
