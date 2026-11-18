import { describe, expect, it } from 'vitest'
import { type CopilotEvent, readEvents, reduceTurn, type Turn } from './useCopilot'

const blank = (): Turn => ({ id: 1, question: 'q', status: 'running', stage: 'sending', sqls: [], models: {} })

/** Streams `chunks` the way a network would: arbitrary byte boundaries, not line boundaries. */
function stream(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c))
      controller.close()
    },
  })
}

async function collect(chunks: string[]): Promise<CopilotEvent[]> {
  const out: CopilotEvent[] = []
  await readEvents(stream(chunks), (e) => out.push(e))
  return out
}

describe('readEvents', () => {
  it('parses one event per line', async () => {
    const events = await collect(['{"type":"status","stage":"a"}\n{"type":"status","stage":"b"}\n'])
    expect(events.map((e) => (e as { stage: string }).stage)).toEqual(['a', 'b'])
  })

  it('reassembles a line split across chunks, including inside a JSON string', async () => {
    const events = await collect(['{"type":"sta', 'tus","stage":"split ', 'here"}\n{"type":"done","elapsed_s":1,"models":{}}\n'])
    expect(events).toHaveLength(2)
    expect((events[0] as { stage: string }).stage).toBe('split here')
  })

  it('handles multi-byte characters split across chunks', async () => {
    const bytes = new TextEncoder().encode('{"type":"status","stage":"₹ done"}\n')
    const cut = bytes.indexOf(0xe2) + 1 // inside the 3-byte rupee sign
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, cut))
        c.enqueue(bytes.slice(cut))
        c.close()
      },
    })
    const out: CopilotEvent[] = []
    await readEvents(body, (e) => out.push(e))
    expect((out[0] as { stage: string }).stage).toBe('₹ done')
  })

  it('flushes a final line that has no trailing newline and skips blank lines', async () => {
    const events = await collect(['\n\n{"type":"heartbeat"}\n\n', '{"type":"error","message":"boom"}'])
    expect(events.map((e) => e.type)).toEqual(['heartbeat', 'error'])
  })

  it('rejects malformed JSON instead of silently dropping it', async () => {
    await expect(collect(['{"type": oops}\n'])).rejects.toThrow()
  })
})

describe('reduceTurn', () => {
  it('builds up a turn from the documented event sequence', () => {
    const events: CopilotEvent[] = [
      { type: 'status', stage: 'routing' },
      { type: 'route', intent: 'analytical', reason: 'numbers', model: 'fake/router' },
      { type: 'sql', attempt: 1, sql: 'SELECT 1', model: 'fake/sql' },
      { type: 'sql', attempt: 2, sql: 'SELECT 2', model: 'fake/repair' },
      { type: 'heartbeat' },
      { type: 'answer', markdown: 'ok', grounded: true, mode: 'model', ungrounded: [] },
      { type: 'done', elapsed_s: 1.5, models: { sql: 'fake/sql' } },
    ]
    const turn = events.reduce(reduceTurn, blank())
    expect(turn.status).toBe('done')
    expect(turn.route?.intent).toBe('analytical')
    expect(turn.sqls.map((s) => s.attempt)).toEqual([1, 2])
    expect(turn.answer?.markdown).toBe('ok')
    expect(turn.models).toEqual({ sql: 'fake/sql' })
    expect(turn.elapsed).toBe(1.5)
  })

  it('ignores heartbeats completely', () => {
    const before = blank()
    expect(reduceTurn(before, { type: 'heartbeat' })).toBe(before)
  })

  it('marks an errored turn and keeps what arrived before the error', () => {
    const turn = [
      { type: 'status', stage: 'routing' },
      { type: 'error', message: 'all models failed' },
    ].reduce((t, e) => reduceTurn(t, e as CopilotEvent), blank())
    expect(turn.status).toBe('error')
    expect(turn.error).toBe('all models failed')
  })
})
