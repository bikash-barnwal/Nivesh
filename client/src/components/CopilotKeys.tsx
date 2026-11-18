import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { put, send } from '../api/client'
import { q } from '../api/queries'
import type { SavedKey, UserSettings } from '../api/types'

const PROVIDERS: Record<string, { label: string; getKey: string }> = {
  gemini: { label: 'Google Gemini', getKey: 'https://aistudio.google.com/apikey' },
}

/** A short hint from the model's name (Google's naming: pro = strongest, flash = balanced, lite = fastest). */
function modelNote(id: string): string {
  const kind = id.includes('-pro') ? 'strongest, slower' : id.includes('lite') ? 'fastest, cheapest' : id.includes('flash') ? 'fast, balanced' : 'experimental'
  const extra = [id.includes('preview') && 'preview, may change', id.endsWith('-latest') && 'always Google’s newest']
  return [kind, ...extra.filter(Boolean)].join(' · ')
}

const label = (p: string) => PROVIDERS[p]?.label ?? p

/** Your own model key (write-only: sent once, never shown again) and the AI model your Copilot uses. */
export function AiKeyAndModel() {
  const qc = useQueryClient()
  const me = useQuery(q.me())
  const providers = me.data?.copilot.providers ?? []
  const [provider, setProvider] = useState<string>('')
  const [key, setKey] = useState('')
  // The model pick shows at once; the server copy catches up on refetch.
  const [picked, setPicked] = useState<string | null>(null)
  const chosen = provider || providers[0] || ''

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['me'] })
    void qc.invalidateQueries({ queryKey: ['copilot', 'models'] })
  }
  const save = useMutation({
    mutationFn: () => put<SavedKey>(`/api/me/llm-keys/${chosen}`, { key }),
    onSuccess: () => {
      setKey('')
      refresh()
    },
  })
  const remove = useMutation({
    mutationFn: (p: string) => send<unknown>('DELETE', `/api/me/llm-keys/${p}`),
    onSuccess: refresh,
  })
  const pickModel = useMutation({
    mutationFn: (model: string | null) => put<UserSettings>('/api/me/settings', { copilot_model: model }),
    onSuccess: refresh,
  })

  if (me.isPending) return <p className="text-sm text-muted">Loading…</p>
  if (me.isError) return <p className="text-sm text-bad">Could not load your account: {me.error.message}</p>
  const { keys_enabled: enabled, keys, uses_server_keys: serverKeys, models } = me.data.copilot
  const options = models[chosen] ?? []
  const current = picked ?? me.data.settings.copilot_model ?? ''

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (key.trim() && chosen) save.mutate()
  }

  return (
    <div className="grid gap-6 text-sm lg:grid-cols-2">
      <section aria-labelledby="key-heading" className="space-y-4">
        <h3 id="key-heading" className="font-semibold">Your API key</h3>
        <p className="text-muted">
          The Copilot runs on your own key, so you control its use and cost. It is tested, stored encrypted, and never
          shown again or shared with anyone.
          {serverKeys && ' Sign-in is off here, so the server keys work too; your own key is used when saved.'}
        </p>

        {!enabled && <p className="rounded border border-warn/50 bg-warn/10 px-3 py-2 text-warn">Own keys are not enabled on this server.</p>}
        {enabled && providers.length === 0 && (
          <p className="rounded border border-warn/50 bg-warn/10 px-3 py-2 text-warn">No provider is offered for your own key on this server.</p>
        )}

        {keys.length > 0 && (
          <ul className="space-y-2" aria-label="Saved keys">
            {keys.map((k) => (
              <li key={k.provider} className="flex items-center justify-between gap-3 rounded border border-line px-3 py-2">
                <div>
                  <div className="font-medium">{label(k.provider)}</div>
                  <div className="text-xs text-muted">
                    ••••{k.last4} · saved {k.created_at?.slice(0, 10)} · test {k.last_status ?? '—'}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => remove.mutate(k.provider)}
                  disabled={remove.isPending}
                  className="rounded border border-line px-2 py-1 text-xs text-muted hover:border-bad hover:text-bad"
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}

        {enabled && providers.length > 0 && (
          <form onSubmit={submit} className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs text-muted">Provider</span>
              <select value={chosen} onChange={(e) => setProvider(e.target.value)} className="w-full rounded border border-line bg-panel-2 px-3 py-2">
                {providers.map((p) => (
                  <option key={p} value={p}>
                    {label(p)}
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-muted">API key</span>
              <input
                type="password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                autoComplete="off"
                spellCheck={false}
                maxLength={300}
                placeholder={keys.some((k) => k.provider === chosen) ? 'Paste a new key to replace the saved one' : 'Paste your key'}
                className="w-full rounded border border-line bg-panel-2 px-3 py-2 font-mono placeholder:font-sans placeholder:text-muted focus:border-accent focus:outline-none"
              />
            </label>
            {PROVIDERS[chosen] && (
              <p className="text-xs text-muted">
                No key yet?{' '}
                <a href={PROVIDERS[chosen].getKey} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                  Get a free {label(chosen)} key
                </a>
                .
              </p>
            )}
            <button type="submit" disabled={!key.trim() || save.isPending} className="rounded bg-accent px-4 py-2 font-semibold text-bg disabled:opacity-40">
              {save.isPending ? 'Testing…' : 'Test and save'}
            </button>
            {save.isError && <p className="text-bad">{save.error.message}</p>}
            {save.isSuccess && <p className="text-good">Key works and is saved.</p>}
          </form>
        )}
      </section>

      <section aria-labelledby="model-heading" className="space-y-4">
        <h3 id="model-heading" className="font-semibold">AI model</h3>
        <p className="text-muted">
          The model your Copilot uses for every step (routing, SQL, the written answer). Only you are affected. Some models
          may refuse free-tier keys; if a question fails, pick another or use Default.
        </p>
        {options.length === 0 ? (
          <p className="text-muted">No models are offered for your own key on this server.</p>
        ) : (
          <label className="block">
            <span className="mb-1 block text-xs text-muted">Model</span>
            <select
              value={current}
              disabled={pickModel.isPending}
              onChange={(e) => {
                setPicked(e.target.value)
                pickModel.mutate(e.target.value || null)
              }}
              className="w-full rounded border border-line bg-panel-2 px-3 py-2"
            >
              <option value="">Default ({options[0]}) · recommended</option>
              {options.map((m) => (
                <option key={m} value={m}>
                  {m} · {modelNote(m)}
                </option>
              ))}
            </select>
          </label>
        )}
        {pickModel.isError && <p className="text-bad">{pickModel.error.message}</p>}
        {pickModel.isSuccess && <p className="text-good">Saved. Your next question uses it.</p>}
      </section>
    </div>
  )
}
