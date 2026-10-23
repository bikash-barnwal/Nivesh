import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, Outlet, useNavigate } from '@tanstack/react-router'
import { useEffect, useRef, useState } from 'react'
import { q } from '../api/queries'
import { useAsOf, useSetAsOf } from '../lib/asof'
import { num } from '../lib/format'
import { Badge } from './ui'

const NAV = [
  { to: '/market', label: 'Market' },
  { to: '/sectors', label: 'Sectors' },
  { to: '/stocks/$symbol', label: 'Stocks', params: { symbol: 'RELIANCE' } },
  { to: '/scans/$scanId', label: 'Scans', params: { scanId: 'early_entry' } },
  { to: '/news', label: 'News' },
  { to: '/strategies', label: 'Strategies' },
  { to: '/copilot', label: 'Copilot' },
  { to: '/paper', label: 'Paper' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/watchlist', label: 'Watchlist' },
] as const

function StockSearch() {
  const navigate = useNavigate()
  const [term, setTerm] = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(term.trim()), 200)
    return () => clearTimeout(t)
  }, [term])
  useEffect(() => {
    const close = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])
  const results = useQuery(q.stockSearch(debounced))
  const go = (symbol: string) => {
    setOpen(false)
    setTerm('')
    void navigate({ to: '/stocks/$symbol', params: { symbol }, search: ((prev: { as_of?: string }) => ({ as_of: prev.as_of })) as never })
  }
  return (
    <div ref={box} className="relative">
      <input
        value={term}
        onChange={(e) => {
          setTerm(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          const first = results.data?.data[0]
          if (e.key === 'Enter' && first) go(first.symbol)
          if (e.key === 'Escape') setOpen(false)
        }}
        placeholder="Stock lookup…"
        aria-label="Stock lookup"
        className="w-44 rounded border border-line bg-panel-2 px-3 py-1.5 text-sm placeholder:text-muted focus:border-accent focus:outline-none"
      />
      {open && debounced && (
        <ul className="absolute right-0 z-30 mt-1 w-64 overflow-hidden rounded border border-line bg-panel shadow-xl">
          {results.isPending && <li className="px-3 py-2 text-sm text-muted">Searching…</li>}
          {results.data?.data.length === 0 && <li className="px-3 py-2 text-sm text-muted">No match</li>}
          {results.data?.data.map((r) => (
            <li key={r.symbol}>
              <button type="button" onClick={() => go(r.symbol)} className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-panel-2">
                <span className="font-medium">{r.symbol}</span>
                <span className="tabular text-muted">₹{num(r.close, 2)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AsOfBar() {
  const asOf = useAsOf()
  const setAsOf = useSetAsOf()
  const fresh = useQuery(q.freshness())
  const history = useQuery({ ...q.regimeHistory(undefined, 750), placeholderData: keepPreviousData })
  const through = fresh.data?.data_through ?? null
  const sessions = (history.data?.data ?? []).map((r) => r.date).sort()
  const index = asOf ? Math.max(0, sessions.findIndex((d) => d >= asOf)) : sessions.length - 1
  const historical = !!asOf && !!through && asOf < through

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-panel/70 px-4 py-2 text-xs text-muted">
      <label className="flex items-center gap-2">
        As of
        <input
          type="date"
          value={asOf ?? through ?? ''}
          max={through ?? undefined}
          onChange={(e) => setAsOf(e.target.value || undefined)}
          className="rounded border border-line bg-panel-2 px-2 py-1 text-text"
        />
      </label>
      {sessions.length > 1 && (
        <input
          type="range"
          min={0}
          max={sessions.length - 1}
          value={index}
          onChange={(e) => setAsOf(sessions[Number(e.target.value)])}
          aria-label="Session slider"
          className="w-48 accent-accent"
        />
      )}
      <button
        type="button"
        onClick={() => setAsOf(undefined)}
        disabled={!asOf}
        className="rounded border border-line px-2 py-1 hover:bg-panel-2 disabled:opacity-40"
      >
        Latest
      </button>
      {historical && <Badge className="border-warn/60 bg-warn/10 text-warn">Historical read</Badge>}
      <span className="ml-auto">Data through {through ?? '—'}</span>
    </div>
  )
}

export function Shell() {
  return (
    <div className="flex min-h-full flex-col">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line bg-panel px-4 py-2.5">
        <Link to="/market" search={(prev: { as_of?: string }) => ({ as_of: prev.as_of })} className="text-lg font-bold tracking-tight text-accent">
          Nivesh
        </Link>
        <nav className="flex gap-1" aria-label="Primary">
          {NAV.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              params={'params' in item ? (item.params as never) : undefined}
              search={((prev: { as_of?: string }) => ({ as_of: prev.as_of })) as never}
              activeOptions={{ includeSearch: false }}
              className="rounded px-3 py-1.5 text-sm text-muted hover:bg-panel-2 hover:text-text"
              activeProps={{ className: 'bg-panel-2 text-text' }}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto">
          <StockSearch />
        </div>
      </header>
      <AsOfBar />
      <main className="mx-auto w-full max-w-[1500px] flex-1 px-4 py-5">
        <Outlet />
      </main>
      <footer className="border-t border-line px-4 py-3 text-xs text-muted">
        Educational analytics, not investment advice. All numbers are computed deterministically from end-of-day NSE data.
      </footer>
    </div>
  )
}
