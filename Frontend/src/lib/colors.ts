export const REGIME_COLORS: Record<string, string> = {
  Capitulation: '#a855f7',
  Bear: '#ef4444',
  Correction: '#f97316',
  Distribution: '#f59e0b',
  'Late Bull': '#eab308',
  Neutral: '#94a3b8',
  Recovery: '#22d3ee',
  'Early Bull': '#14b8a6',
  'Broad Bull': '#22c55e',
  'Momentum Bull': '#84cc16',
}
export const REGIME_ORDER = Object.keys(REGIME_COLORS)

export const SEVERITY_STYLE: Record<string, string> = {
  CRITICAL: 'border-bad/60 bg-bad/10 text-bad',
  WARNING: 'border-warn/60 bg-warn/10 text-warn',
  WATCH: 'border-yellow-300/50 bg-yellow-300/10 text-yellow-200',
  INFO: 'border-info/60 bg-info/10 text-info',
}

export const POSTURE_STYLE: Record<string, string> = {
  'CAPITAL PRESERVATION': 'border-bad/60 bg-bad/10 text-bad',
  DEFENSIVE: 'border-orange-400/60 bg-orange-400/10 text-orange-300',
  SELECTIVE: 'border-warn/60 bg-warn/10 text-warn',
  'RISK ON': 'border-good/60 bg-good/10 text-good',
}

export const VERDICT_STYLE: Record<string, string> = {
  'HOLD-ADD': 'border-good/60 bg-good/10 text-good',
  NEUTRAL: 'border-line bg-panel-2 text-muted',
  AVOID: 'border-bad/60 bg-bad/10 text-bad',
}

export const TREND_STYLE: Record<string, string> = {
  ACCELERATING: 'border-good/60 bg-good/10 text-good',
  IMPROVING: 'border-good/40 bg-good/5 text-good',
  STABLE: 'border-line bg-panel-2 text-muted',
  FADING: 'border-warn/60 bg-warn/10 text-warn',
  DETERIORATING: 'border-bad/60 bg-bad/10 text-bad',
}

export const STATUS_STYLE: Record<string, string> = {
  PASS: 'border-good/60 bg-good/10 text-good',
  FAIL: 'border-bad/60 bg-bad/10 text-bad',
  UNKNOWN: 'border-line bg-panel-2 text-muted',
  INCOMPLETE: 'border-warn/60 bg-warn/10 text-warn',
}

/** How to read a 0-100 score: `good-high` (more is healthier), `bad-high` (more is riskier), `neutral` (just a level). */
export type Tone = 'good-high' | 'bad-high' | 'neutral'

/** Heat colour for a 0-100 score. */
export function scoreColor(v: number | null | undefined, tone: Tone = 'good-high'): string {
  if (v == null) return '#4b5870'
  if (tone === 'neutral') return '#60a5fa'
  const strong = tone === 'good-high' ? v >= 66 : v < 40
  const weak = tone === 'good-high' ? v < 40 : v >= 66
  return strong ? '#34d399' : weak ? '#f87171' : '#fbbf24'
}

/** Engines/gauges where a high reading means more risk, not more strength. */
export const ENGINE_TONE: Record<string, Tone> = {
  selling_pressure: 'bad-high',
  risk_exhaustion: 'bad-high',
  exhaustion: 'bad-high',
  volatility: 'neutral',
}

export const gainClass = (v: number | null | undefined) =>
  v == null ? 'text-muted' : v > 0 ? 'text-good' : v < 0 ? 'text-bad' : 'text-text'
