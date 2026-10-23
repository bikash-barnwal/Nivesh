import type { UseQueryResult } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { ApiError } from '../api/client'
import { scoreColor, type Tone } from '../lib/colors'

export function Badge({ children, className = '', title }: { children: ReactNode; className?: string; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${className}`}
    >
      {children}
    </span>
  )
}

export function Card({
  title,
  hint,
  right,
  children,
  className = '',
}: {
  title?: ReactNode
  hint?: ReactNode
  right?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-lg border border-line bg-panel ${className}`}>
      {(title || right) && (
        <header className="flex items-baseline justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted">
            {title} {hint && <span className="ml-1 font-normal normal-case tracking-normal">{hint}</span>}
          </h2>
          {right}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

/** Tiny inline trend line; no axes, just shape. */
export function Spark({
  values,
  color = '#7aa2ff',
  width = 120,
  height = 28,
}: {
  values: (number | null)[]
  color?: string
  width?: number
  height?: number
}) {
  const pts = values.filter((v): v is number => v != null)
  if (pts.length < 2) return <svg width={width} height={height} aria-hidden />
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const span = max - min || 1
  const d = pts
    .map((v, i) => `${i === 0 ? 'M' : 'L'}${((i / (pts.length - 1)) * width).toFixed(1)},${(height - 2 - ((v - min) / span) * (height - 4)).toFixed(1)}`)
    .join(' ')
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="trend">
      <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  )
}

/** Horizontal 0-100 bar with the value printed beside it. */
export function ScoreBar({
  value,
  width = 'w-24',
  tone = 'good-high',
}: {
  value: number | null | undefined
  width?: string
  tone?: Tone
}) {
  const v = value == null ? 0 : Math.max(0, Math.min(100, value))
  return (
    <div className="flex items-center gap-2">
      <div className={`h-2 ${width} overflow-hidden rounded bg-panel-2`}>
        <div className="h-full rounded" style={{ width: `${v}%`, background: scoreColor(value, tone) }} />
      </div>
      <span className="tabular w-8 text-right text-xs">{value == null ? '—' : value.toFixed(0)}</span>
    </div>
  )
}

export function Skeleton({ className = 'h-24' }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-panel-2 ${className}`} />
}

/** Renders loading / error / empty / content for a query, keeping the previous content while a new date loads. */
export function QueryState<T>({
  query,
  children,
  isEmpty,
  empty = 'Not available for this date. The analytics may not be built yet.',
  skeleton,
}: {
  query: UseQueryResult<T>
  children: (data: T) => ReactNode
  isEmpty?: (data: T) => boolean
  empty?: string
  skeleton?: ReactNode
}) {
  if (query.isPending) return <>{skeleton ?? <Skeleton />}</>
  if (query.isError) {
    const err = query.error
    const msg = err instanceof ApiError ? err.message : 'Request failed'
    return (
      <p className="rounded border border-line bg-panel-2 px-3 py-2 text-sm text-muted">
        {err instanceof ApiError && err.status === 404 ? empty : msg}
      </p>
    )
  }
  if (isEmpty?.(query.data)) return <p className="px-1 py-2 text-sm text-muted">{empty}</p>
  return <div className={query.isPlaceholderData ? 'opacity-60 transition-opacity' : ''}>{children(query.data)}</div>
}
