const dash = '—'

/** 0.1234 -> "12.3%"; pass `sign` to show an explicit + for gains. */
export function pct(x: number | null | undefined, digits = 1, sign = false): string {
  if (x == null || Number.isNaN(x)) return dash
  const v = (x * 100).toFixed(digits)
  return `${sign && x > 0 ? '+' : ''}${v}%`
}

export function num(x: number | null | undefined, digits = 0): string {
  if (x == null || Number.isNaN(x)) return dash
  return x.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function signed(x: number | null | undefined, digits = 1): string {
  if (x == null || Number.isNaN(x)) return dash
  return `${x > 0 ? '+' : ''}${x.toFixed(digits)}`
}

export function rupees(x: number | null | undefined, digits = 2): string {
  return x == null || Number.isNaN(x) ? dash : `₹${num(x, digits)}`
}

export function label(snake: string): string {
  return snake.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export const isIsoDate = (s: unknown): s is string => typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s)

export const DIRECTION_STYLE: Record<string, string> = {
  positive: 'border-good/60 bg-good/10 text-good',
  negative: 'border-bad/60 bg-bad/10 text-bad',
  neutral: 'border-line bg-panel-2 text-muted',
}

export const directionStyle = (direction: string): string => DIRECTION_STYLE[direction] ?? DIRECTION_STYLE.neutral

/** Rupee crore amount: 1234.5 -> "₹1,234.5 cr". */
export const crore = (x: number | null | undefined): string => (x == null || Number.isNaN(x) ? dash : `₹${num(x, x % 1 ? 1 : 0)} cr`)

/** Only http(s) links render as anchors; javascript: and data: URLs are dropped. */
export const safeUrl = (url: string | null | undefined): string | null => (url && /^https?:\/\//i.test(url) ? url : null)
