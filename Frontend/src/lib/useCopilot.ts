import { useCallback, useRef, useState } from 'react'

export type Evidence = {
  evidence_id: string
  kind: 'analytical' | 'knowledge' | 'refusal'
  question: string
  data_through: string | null
  executed_at: string
  sql?: string
  columns?: string[]
  rows?: unknown[][]
  row_count?: number
  truncated?: boolean
  source_views?: string[]
  calculation?: string
  heal_log?: { attempt: number; sql: string; error: string }[]
  passages?: { source: string; heading: string; text: string; score: number }[]
  reason?: string
  error?: string
  confidence?: string
}

export type CopilotEvent =
  | { type: 'status'; stage: string }
  | { type: 'route'; intent: string; reason: string; model: string }
  | { type: 'sql'; attempt: number; sql: string; model: string }
  | { type: 'evidence'; evidence: Evidence }
  | { type: 'answer'; markdown: string; grounded: boolean; mode: 'model' | 'deterministic'; ungrounded: string[]; model?: string; note?: string }
  | { type: 'done'; elapsed_s: number; models: Record<string, string> }
  | { type: 'error'; message: string }
  | { type: 'heartbeat' }

export type Turn = {
  id: number
  question: string
  status: 'running' | 'done' | 'error'
  stage: string
  route?: { intent: string; reason: string; model: string }
  sqls: { attempt: number; sql: string; model: string }[]
  evidence?: Evidence
  answer?: Extract<CopilotEvent, { type: 'answer' }>
  models: Record<string, string>
  elapsed?: number
  error?: string
}

/** Applies one streamed event to a turn. Heartbeats only keep the connection alive and are ignored. */
export function reduceTurn(turn: Turn, event: CopilotEvent): Turn {
  switch (event.type) {
    case 'status':
      return { ...turn, stage: event.stage }
    case 'route':
      return { ...turn, route: { intent: event.intent, reason: event.reason, model: event.model } }
    case 'sql':
      return { ...turn, sqls: [...turn.sqls, { attempt: event.attempt, sql: event.sql, model: event.model }] }
    case 'evidence':
      return { ...turn, evidence: event.evidence }
    case 'answer':
      return { ...turn, answer: event }
    case 'done':
      return { ...turn, status: 'done', stage: 'done', models: event.models, elapsed: event.elapsed_s }
    case 'error':
      return { ...turn, status: 'error', error: event.message }
    default:
      return turn
  }
}

/** Splits a streamed body into NDJSON events, tolerating chunks that end mid-line. */
export async function readEvents(body: ReadableStream<Uint8Array>, onEvent: (e: CopilotEvent) => void): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const flush = (line: string) => {
    const text = line.trim()
    if (text) onEvent(JSON.parse(text) as CopilotEvent)
  }
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let newline = buffer.indexOf('\n')
    while (newline >= 0) {
      flush(buffer.slice(0, newline))
      buffer = buffer.slice(newline + 1)
      newline = buffer.indexOf('\n')
    }
  }
  flush(buffer)
}

export function useCopilot() {
  const [turns, setTurns] = useState<Turn[]>([])
  const nextId = useRef(1)
  const abort = useRef<AbortController | null>(null)

  const ask = useCallback(async (question: string) => {
    const id = nextId.current++
    const initial: Turn = { id, question, status: 'running', stage: 'sending', sqls: [], models: {} }
    setTurns((t) => [...t, initial])
    const update = (fn: (t: Turn) => Turn) => setTurns((all) => all.map((t) => (t.id === id ? fn(t) : t)))
    abort.current = new AbortController()
    try {
      const res = await fetch('/api/copilot/ask', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ question }),
        signal: abort.current.signal,
      })
      if (!res.ok || !res.body) {
        const detail = await res.text()
        update((t) => reduceTurn(t, { type: 'error', message: `Request failed (${res.status}): ${detail.slice(0, 200)}` }))
        return id
      }
      await readEvents(res.body, (event) => update((t) => reduceTurn(t, event)))
      update((t) => (t.status === 'running' ? { ...t, status: 'error', error: 'The stream ended unexpectedly.' } : t))
    } catch (e) {
      if ((e as Error).name !== 'AbortError') update((t) => reduceTurn(t, { type: 'error', message: (e as Error).message }))
    }
    return id
  }, [])

  const stop = useCallback(() => abort.current?.abort(), [])
  const clear = useCallback(() => setTurns([]), [])
  return { turns, ask, stop, clear }
}
