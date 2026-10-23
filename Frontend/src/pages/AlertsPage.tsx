import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { type Envelope, post, send } from '../api/client'
import { lab, ops } from '../api/queries'
import { Badge, Card, QueryState, Skeleton } from '../components/ui'
import { useAsOf } from '../lib/asof'
import { SEVERITY_STYLE } from '../lib/colors'
import { label } from '../lib/format'

const SCANS = ['early_entry', 'breakout_volume', 'vcp_tight_base', 'leaders_near_highs', 'accumulation']
const KIND_HELP: Record<string, string> = {
  regime_change: 'When the market regime changes',
  market_event: 'When a market event first fires (at or above a severity)',
  scan_entry: 'When a stock newly enters a scan’s top 20',
  watchlist: 'Lifecycle changes, circuit hits and upcoming corporate actions for watchlist stocks',
  strategy_signal: 'When a strategy has a new entry candidate',
}

function Brief() {
  const asOf = useAsOf()
  const brief = useQuery(ops.brief(asOf))
  return (
    <Card title="Daily brief" hint="(templated from the analytics; no model involved)">
      <QueryState query={brief} skeleton={<Skeleton className="h-32" />}>
        {(res) => (
          <div className="space-y-2 text-sm leading-relaxed [&_strong]:font-semibold">
            <h3 className="text-base font-semibold">{res.data.title}</h3>
            <Markdown remarkPlugins={[remarkGfm]} components={{ p: (p) => <p className="whitespace-pre-wrap" {...p} /> }}>
              {res.data.markdown}
            </Markdown>
          </div>
        )}
      </QueryState>
    </Card>
  )
}

function Inbox() {
  const client = useQueryClient()
  const inbox = useQuery(ops.inbox())
  const run = useMutation({
    mutationFn: () => post<Envelope<Record<string, number>>>('/api/alerts/run', {}),
    onSuccess: () => client.invalidateQueries({ queryKey: ['alerts'] }),
  })
  const read = useMutation({
    mutationFn: () => post('/api/alerts/inbox/read', {}),
    onSuccess: () => client.invalidateQueries({ queryKey: ['alerts', 'inbox'] }),
  })
  return (
    <Card
      title="Inbox"
      hint={inbox.data ? `(${inbox.data.unread} unread)` : ''}
      right={
        <div className="flex gap-2">
          <button type="button" onClick={() => run.mutate()} disabled={run.isPending} className="rounded border border-line px-2 py-1 text-xs hover:bg-panel-2 disabled:opacity-50">
            {run.isPending ? 'Checking…' : 'Check now'}
          </button>
          <button type="button" onClick={() => read.mutate()} className="rounded border border-line px-2 py-1 text-xs hover:bg-panel-2">Mark all read</button>
        </div>
      }
    >
      {run.data && (
        <p className="mb-3 text-xs text-muted" role="status">
          Checked {run.data.data.rules} rules: {run.data.data.new} new, {run.data.data.duplicates} already delivered.
        </p>
      )}
      <QueryState query={inbox} isEmpty={(r) => r.data.length === 0} empty="Nothing yet. Add rules and press Check now; the daily pipeline also checks automatically." skeleton={<Skeleton className="h-24" />}>
        {(res) => (
          <ul className="max-h-[26rem] space-y-2 overflow-y-auto pr-1">
            {res.data.map((a) => (
              <li key={a.id} className={`rounded-lg border px-3 py-2 ${a.read ? 'border-line' : 'border-accent/50 bg-accent/5'}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge className={SEVERITY_STYLE[a.severity] ?? 'border-line bg-panel-2 text-muted'}>{a.severity}</Badge>
                  <span className="font-medium">{a.title}</span>
                  <span className="ml-auto text-xs text-muted">{a.as_of}</span>
                </div>
                <p className="mt-1 text-sm text-muted">{a.body}</p>
                {(a.delivered_to.length > 0 || a.errors.length > 0) && (
                  <p className="mt-1 text-[11px] text-muted">
                    {a.delivered_to.length > 0 && <>Sent to {a.delivered_to.join(', ')}. </>}
                    {a.errors.length > 0 && <span className="text-bad">{a.errors.join(' · ')}</span>}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Card>
  )
}

function Rules() {
  const client = useQueryClient()
  const rules = useQuery(ops.alertRules())
  const strategies = useQuery(lab.strategies())
  const [name, setName] = useState('')
  const [kind, setKind] = useState('regime_change')
  const [scanId, setScanId] = useState(SCANS[0])
  const [minScore, setMinScore] = useState(70)
  const [severity, setSeverity] = useState('WARNING')
  const [strategyId, setStrategyId] = useState('')
  const refresh = () => client.invalidateQueries({ queryKey: ['alerts'] })
  const create = useMutation({
    mutationFn: () =>
      post('/api/alerts', {
        name: name || label(kind),
        kind,
        scan_id: kind === 'scan_entry' ? scanId : undefined,
        min_score: kind === 'scan_entry' ? minScore : 0,
        severity_min: kind === 'market_event' ? severity : undefined,
        strategy_id: kind === 'strategy_signal' ? strategyId || strategies.data?.data[0]?.id : undefined,
      }),
    onSuccess: () => {
      setName('')
      void refresh()
    },
  })
  const remove = useMutation({ mutationFn: (id: number) => send('DELETE', `/api/alerts/${id}`), onSuccess: refresh })
  const defaults = useMutation({ mutationFn: () => post('/api/alerts/defaults', {}), onSuccess: refresh })
  const field = 'rounded border border-line bg-panel-2 px-2 py-1.5 text-sm'
  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate()
  }
  return (
    <Card title="Alert rules" right={<button type="button" onClick={() => defaults.mutate()} className="rounded border border-line px-2 py-1 text-xs hover:bg-panel-2">Add default rules</button>}>
      <QueryState query={rules} isEmpty={(r) => r.data.length === 0} empty="No rules yet. Add the defaults or create your own below." skeleton={<Skeleton className="h-20" />}>
        {(res) => (
          <>
            <ul className="divide-y divide-line">
              {res.data.map((r) => (
                <li key={r.id} className="flex items-center gap-3 py-2 text-sm">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium">{r.name}</div>
                    <div className="truncate text-xs text-muted">
                      {KIND_HELP[r.kind] ?? r.kind}
                      {r.scan_id ? ` · ${r.scan_id} ≥ ${r.min_score}` : ''}
                      {typeof r.params.severity_min === 'string' ? ` · ≥ ${r.params.severity_min}` : ''}
                      {typeof r.params.strategy_id === 'string' ? ` · ${r.params.strategy_id}` : ''}
                    </div>
                  </div>
                  <button type="button" onClick={() => remove.mutate(r.id)} className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-bad">Delete</button>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-xs text-muted">Delivery: {res.channels.join(', ')}. Add Telegram or email in <code>Backend/.env</code> (see <code>.env.example</code>).</p>
          </>
        )}
      </QueryState>
      <form onSubmit={submit} className="mt-4 flex flex-wrap items-end gap-2 border-t border-line pt-4">
        <label className="text-xs text-muted">Type
          <select aria-label="Rule type" className={`${field} mt-1 block`} value={kind} onChange={(e) => setKind(e.target.value)}>
            {Object.keys(KIND_HELP).map((k) => <option key={k} value={k}>{label(k)}</option>)}
          </select>
        </label>
        {kind === 'scan_entry' && (
          <>
            <label className="text-xs text-muted">Scan
              <select aria-label="Scan" className={`${field} mt-1 block`} value={scanId} onChange={(e) => setScanId(e.target.value)}>
                {SCANS.map((s) => <option key={s} value={s}>{label(s)}</option>)}
              </select>
            </label>
            <label className="text-xs text-muted">Min score
              <input aria-label="Minimum score" type="number" min={0} max={100} className={`${field} mt-1 block w-24`} value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} />
            </label>
          </>
        )}
        {kind === 'market_event' && (
          <label className="text-xs text-muted">At least
            <select aria-label="Minimum severity" className={`${field} mt-1 block`} value={severity} onChange={(e) => setSeverity(e.target.value)}>
              {['CRITICAL', 'WARNING', 'WATCH', 'INFO'].map((s) => <option key={s}>{s}</option>)}
            </select>
          </label>
        )}
        {kind === 'strategy_signal' && (
          <label className="text-xs text-muted">Strategy
            <select aria-label="Strategy" className={`${field} mt-1 block`} value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
              {strategies.data?.data.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
            </select>
          </label>
        )}
        <label className="text-xs text-muted">Name
          <input aria-label="Rule name" className={`${field} mt-1 block w-56`} placeholder={label(kind)} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <button type="submit" disabled={create.isPending || (kind === 'strategy_signal' && !(strategyId || strategies.data?.data.length))} className="rounded bg-accent px-3 py-1.5 text-sm font-semibold text-bg disabled:opacity-50">Add rule</button>
        {create.isError && <span className="text-xs text-bad" role="alert">{(create.error as Error).message}</span>}
      </form>
    </Card>
  )
}

export function AlertsPage() {
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <div className="space-y-4">
        <Brief />
        <Rules />
      </div>
      <Inbox />
    </div>
  )
}
