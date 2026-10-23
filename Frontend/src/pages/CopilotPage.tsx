import { useQuery } from '@tanstack/react-query'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { q } from '../api/queries'
import { Badge, Card } from '../components/ui'
import { type Evidence, type Turn, useCopilot } from '../lib/useCopilot'

const EXAMPLES = [
  'Top 5 stocks by 21-day return on the latest date, liquid stocks only',
  'How many stocks are above their 200-day average?',
  'What is the current market regime and how long has it lasted?',
  'Which sectors are ranked highest right now?',
  'How is RVol20 defined?',
]

const TABS = ['SQL', 'Result', 'JSON', 'Freshness'] as const
type Tab = (typeof TABS)[number]

function cell(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'number') return Number.isInteger(v) ? v.toLocaleString('en-IN') : v.toLocaleString('en-IN', { maximumFractionDigits: 4 })
  return String(v)
}

function AnswerBody({ markdown }: { markdown: string }) {
  return (
    <div className="space-y-2 text-sm leading-relaxed [&_strong]:font-semibold">
      <Markdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: (p) => <div className="overflow-auto rounded border border-line"><table className="tabular w-full text-xs" {...p} /></div>,
          th: (p) => <th className="border-b border-line bg-panel-2 px-2 py-1 text-left text-[11px] uppercase text-muted" {...p} />,
          td: (p) => <td className="border-b border-line/50 px-2 py-1" {...p} />,
          code: (p) => <code className="rounded bg-panel-2 px-1 text-xs" {...p} />,
          p: (p) => <p className="whitespace-pre-wrap" {...p} />,
          ul: (p) => <ul className="list-inside list-disc" {...p} />,
        }}
      >
        {markdown}
      </Markdown>
    </div>
  )
}

function TurnView({ turn, selected, onSelect }: { turn: Turn; selected: boolean; onSelect: () => void }) {
  const a = turn.answer
  return (
    <div className="space-y-2">
      <div className="ml-auto max-w-[85%] rounded-lg bg-accent/15 px-3 py-2 text-sm">{turn.question}</div>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className={`block w-full rounded-lg border px-3 py-2.5 text-left ${selected ? 'border-accent/60 bg-panel-2' : 'border-line bg-panel hover:border-line/80'}`}
      >
        {turn.status === 'running' && !a && <p className="text-sm text-muted">… {turn.stage}</p>}
        {turn.status === 'error' && <p className="text-sm text-bad" role="alert">{turn.error}</p>}
        {a && <AnswerBody markdown={a.markdown} />}
        {a && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted">
            {a.grounded && a.mode === 'model' && <Badge className="border-good/60 bg-good/10 text-good">Numbers verified against evidence</Badge>}
            {a.mode === 'deterministic' && !a.grounded && (
              <Badge className="border-warn/60 bg-warn/10 text-warn" title={`Rejected numbers: ${a.ungrounded.join(', ')}`}>
                Model wording rejected — raw result shown
              </Badge>
            )}
            {a.mode === 'deterministic' && a.grounded && <Badge className="border-line bg-panel-2 text-muted">Rendered directly from evidence</Badge>}
            {turn.route && <span>route: {turn.route.intent}</span>}
            {turn.elapsed != null && <span>{turn.elapsed}s</span>}
            <span className="ml-auto">Click for evidence</span>
          </div>
        )}
      </button>
    </div>
  )
}

function EvidencePanel({ turn }: { turn: Turn | undefined }) {
  const [tab, setTab] = useState<Tab>('SQL')
  if (!turn) return <p className="text-sm text-muted">Ask a question, then select an answer to inspect exactly how it was produced.</p>
  const ev: Evidence | undefined = turn.evidence
  return (
    <div className="space-y-3">
      <div role="tablist" className="flex gap-1">
        {TABS.map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`rounded px-3 py-1 text-xs ${tab === t ? 'bg-panel-2 text-text' : 'text-muted hover:text-text'}`}>
            {t}
          </button>
        ))}
      </div>
      {!ev && <p className="text-sm text-muted">No evidence yet.</p>}
      {ev && tab === 'SQL' && (
        <div className="space-y-3">
          {ev.kind === 'knowledge' && <p className="text-sm text-muted">Answered from documents, not from a query.</p>}
          {ev.kind === 'refusal' && <p className="text-sm text-muted">{ev.reason}</p>}
          {turn.sqls.map((s) => {
            const failure = ev.heal_log?.find((h) => h.attempt === s.attempt)
            return (
              <div key={s.attempt}>
                <div className="mb-1 flex items-center gap-2 text-[11px] uppercase tracking-wide text-muted">
                  Attempt {s.attempt} · {s.model}
                  {failure ? <Badge className="border-bad/60 bg-bad/10 text-bad">failed</Badge> : <Badge className="border-good/60 bg-good/10 text-good">ran</Badge>}
                </div>
                <pre className="overflow-auto whitespace-pre-wrap break-words rounded border border-line bg-bg p-3 text-xs">{s.sql}</pre>
                {failure && <p className="mt-1 text-xs text-bad">{failure.error}</p>}
              </div>
            )
          })}
          {ev.calculation && <p className="text-xs text-muted">What it computes: {ev.calculation}</p>}
        </div>
      )}
      {ev && tab === 'Result' && (
        ev.kind === 'knowledge' ? (
          <ul className="space-y-3 text-xs">
            {ev.passages?.map((p) => (
              <li key={`${p.source}-${p.heading}`} className="rounded border border-line p-2">
                <div className="mb-1 font-semibold">{p.heading} <span className="font-normal text-muted">· {p.source}</span></div>
                <p className="whitespace-pre-wrap text-muted">{p.text.slice(0, 600)}</p>
              </li>
            ))}
          </ul>
        ) : (ev.rows?.length ?? 0) === 0 ? (
          <p className="text-sm text-muted">No rows.</p>
        ) : (
          <div className="max-h-[28rem] overflow-auto rounded border border-line">
            <table className="tabular w-full text-xs">
              <thead className="sticky top-0 bg-panel-2"><tr>{ev.columns?.map((c) => <th key={c} className="px-2 py-1 text-left text-[11px] uppercase text-muted">{c}</th>)}</tr></thead>
              <tbody>
                {ev.rows?.slice(0, 200).map((r, i) => (
                  <tr key={i} className="border-t border-line/50">{r.map((v, j) => <td key={j} className="whitespace-nowrap px-2 py-1">{cell(v)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
      {ev && tab === 'JSON' && <pre className="max-h-[28rem] overflow-auto rounded border border-line bg-bg p-3 text-xs">{JSON.stringify(ev, null, 2)}</pre>}
      {ev && tab === 'Freshness' && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <dt className="text-muted">Data valid through</dt><dd className="tabular">{ev.data_through ?? '—'}</dd>
          <dt className="text-muted">Executed at</dt><dd className="tabular">{ev.executed_at}</dd>
          <dt className="text-muted">Evidence id</dt><dd className="tabular">{ev.evidence_id}</dd>
          <dt className="text-muted">Source views</dt><dd>{ev.source_views?.join(', ') || '—'}</dd>
          <dt className="text-muted">Rows</dt><dd>{ev.row_count ?? 0}{ev.truncated ? ' (truncated at the row cap)' : ''}</dd>
          <dt className="text-muted">Confidence</dt><dd>{ev.confidence ?? '—'}</dd>
          <dt className="text-muted">Models</dt>
          <dd>{Object.entries(turn.models).map(([role, m]) => <div key={role}><span className="text-muted">{role}:</span> {m}</div>)}</dd>
        </dl>
      )}
    </div>
  )
}

export function CopilotPage() {
  const { turns, ask, stop, clear } = useCopilot()
  const models = useQuery(q.copilotModels())
  const [text, setText] = useState('')
  const [selected, setSelected] = useState<number | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const busy = turns.some((t) => t.status === 'running')

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [turns])

  const submit = async (e?: FormEvent, question = text) => {
    e?.preventDefault()
    const value = question.trim()
    if (!value || busy) return
    setText('')
    const id = await ask(value)
    setSelected(id)
  }

  const roles = models.data?.data.roles ?? []
  const missing = [...new Set(roles.filter((r) => r.position === 'primary' && !r.local && !r.key_present).map((r) => r.key_env))]
  const current = turns.find((t) => t.id === selected) ?? turns[turns.length - 1]

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_440px]">
      <Card title="Copilot" hint="(answers come from SQL the database ran, never from model memory)" right={turns.length > 0 && <button type="button" onClick={clear} className="text-xs text-muted hover:text-text">Clear</button>}>
        {missing.length > 0 && (
          <p className="mb-3 rounded border border-warn/50 bg-warn/10 px-3 py-2 text-sm text-warn">
            No API key found for: {missing.join(', ')}. Add it to <code>Backend/.env</code>, then restart the API. Run <code>uv run nivesh llm-ping</code> to check.
          </p>
        )}
        <div className="flex h-[32rem] flex-col gap-4 overflow-y-auto pr-1" aria-live="polite">
          {turns.length === 0 && (
            <div className="space-y-3 text-sm text-muted">
              <p>Ask about the loaded market data. Try:</p>
              <div className="flex flex-wrap gap-2">
                {EXAMPLES.map((ex) => (
                  <button key={ex} type="button" onClick={() => void submit(undefined, ex)} className="rounded-full border border-line px-3 py-1 text-left text-xs hover:bg-panel-2 hover:text-text">
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t) => (
            <TurnView key={t.id} turn={t} selected={current?.id === t.id} onSelect={() => setSelected(t.id)} />
          ))}
          <div ref={bottom} />
        </div>
        <form onSubmit={(e) => void submit(e)} className="mt-3 flex gap-2">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Ask about prices, returns, sectors, regimes, scans…"
            aria-label="Question"
            maxLength={1000}
            className="flex-1 rounded border border-line bg-panel-2 px-3 py-2 text-sm placeholder:text-muted focus:border-accent focus:outline-none"
          />
          {busy ? (
            <button type="button" onClick={stop} className="rounded border border-line px-4 py-2 text-sm hover:bg-panel-2">Stop</button>
          ) : (
            <button type="submit" disabled={!text.trim()} className="rounded bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-40">Ask</button>
          )}
        </form>
        <p className="mt-2 text-xs text-muted">Educational analytics, not investment advice. Hosted models only ever see market data, schema and docs.</p>
      </Card>
      <Card title="Evidence">
        <EvidencePanel turn={current} />
      </Card>
    </div>
  )
}
