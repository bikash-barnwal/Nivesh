import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { send } from '../api/client'
import { q } from '../api/queries'
import { Card, QueryState, Skeleton } from '../components/ui'

export function WatchlistPage() {
  const client = useQueryClient()
  const watch = useQuery(q.watchlist())
  const remove = useMutation({
    mutationFn: (symbol: string) => send('DELETE', `/api/watchlist/${symbol}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['watchlist'] }),
  })
  return (
    <Card title="Watchlist" hint="(stored locally in SQLite)">
      <QueryState query={watch} isEmpty={(r) => r.data.length === 0} empty="Nothing here yet. Open a stock and press Watch." skeleton={<Skeleton className="h-24" />}>
        {(res) => (
          <ul className="divide-y divide-line">
            {res.data.map((w) => (
              <li key={w.symbol} className="flex items-center justify-between py-2">
                <Link
                  to="/stocks/$symbol"
                  params={{ symbol: w.symbol }}
                  search={((prev: { as_of?: string }) => ({ as_of: prev.as_of })) as never}
                  className="font-medium text-accent hover:underline"
                >
                  {w.symbol}
                </Link>
                <span className="text-xs text-muted">added {w.created_at}</span>
                <button
                  type="button"
                  onClick={() => remove.mutate(w.symbol)}
                  className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-bad"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Card>
  )
}
