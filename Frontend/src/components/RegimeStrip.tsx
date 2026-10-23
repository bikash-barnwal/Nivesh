import type { RegimeRow } from '../api/types'
import { REGIME_COLORS, REGIME_ORDER } from '../lib/colors'

/** One coloured cell per session, oldest to newest. Clicking a cell time-travels to that date. */
export function RegimeStrip({
  rows,
  selected,
  onPick,
}: {
  rows: RegimeRow[]
  selected?: string | null
  onPick: (date: string) => void
}) {
  const ordered = [...rows].sort((a, b) => a.date.localeCompare(b.date))
  return (
    <div>
      <div className="flex h-7 gap-px overflow-hidden rounded" role="list" aria-label="Regime history">
        {ordered.map((r) => (
          <button
            key={r.date}
            type="button"
            role="listitem"
            title={`${r.date} · ${r.regime}`}
            onClick={() => onPick(r.date)}
            className={`min-w-px flex-1 transition-opacity hover:opacity-80 ${selected === r.date ? 'outline outline-2 outline-white' : ''}`}
            style={{ background: REGIME_COLORS[r.regime] ?? '#475569' }}
          />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
        {REGIME_ORDER.map((name) => (
          <li key={name} className="flex items-center gap-1.5">
            <span className="inline-block size-2 rounded-sm" style={{ background: REGIME_COLORS[name] }} />
            {name}
          </li>
        ))}
      </ul>
    </div>
  )
}
