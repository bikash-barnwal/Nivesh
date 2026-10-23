import type { Envelope } from './client'

export type Freshness = { data_through: string | null; views: Record<string, string | null> }

// ---- market -----------------------------------------------------------------------------------------------------
export type RegimeRow = {
  date: string
  regime: string
  posture: string
  confidence: number
  sessions_active: number
  n_stocks: number
  pct_above_sma20: number | null
  pct_above_sma50: number | null
  pct_above_sma200: number | null
  adv_share: number | null
  idx_close: number | null
  idx_drawdown: number | null
}

export type ActiveEvent = { event: string; severity: Severity; confidence: number; streak: number }
export type Severity = 'CRITICAL' | 'WARNING' | 'WATCH' | 'INFO'

export type OverviewData = { regime: RegimeRow; active_events: ActiveEvent[]; narrative: string } | null

export type EngineRow = {
  date: string
  engine: string
  value: number | null
  delta_10: number | null
  label: string | null
  spark?: number[]
}

export type HorizonStats = {
  episodes?: number
  med_1w?: number | null
  hit_1w?: number | null
  n_1w?: number
  med_1m?: number | null
  hit_1m?: number | null
  n_1m?: number
  med_3m?: number | null
  hit_3m?: number | null
  n_3m?: number
}

export type EventRow = {
  date: string
  event: string
  severity: Severity
  horizon: string
  confidence: number
  description: string
  streak: number
  outcomes: HorizonStats
  recent_occurrences: { date: string; fwd_1m: number | null }[]
  actions: { short?: string; swing?: string; positional?: string }
}
export type EventsResponse = Envelope<EventRow[]> & {
  baseline: Partial<Record<'med_1w' | 'hit_1w' | 'med_1m' | 'hit_1m' | 'med_3m' | 'hit_3m', number | null>>
}

export type AnalogueRow = {
  date: string
  analogue_date: string
  rank: number
  distance: number
  similarity_pct: number
  fwd_1w: number | null
  fwd_1m: number | null
  fwd_3m: number | null
  regime_then: string | null
}
export type AnalogueSummary = {
  matches: number
  median_1m: number | null
  share_positive_1m: number | null
  median_3m: number | null
  share_positive_3m: number | null
  note: string
}
export type AnalogueResponse = Envelope<AnalogueRow[]> & { summary: AnalogueSummary }

export type EngineCatalog = {
  window_sessions: number
  minimum_sessions: number
  engines: Record<string, string[]>
  label_bands: Record<string, [number, number]>
}

// ---- sectors ----------------------------------------------------------------------------------------------------
export type SectorRow = {
  date: string
  sector: string
  n_stocks: number
  n_sectors: number | null
  rank: number | null
  rank_1w: number | null
  rank_1m: number | null
  rank_2m: number | null
  rank_1q: number | null
  d1w: number | null
  d1m: number | null
  d1q: number | null
  rs_trend: string | null
  verdict: string | null
  days_in_verdict: number | null
  strength: number | null
  rs: number | null
  participation: number | null
  flow: number | null
  leadership: number | null
  exhaustion: number | null
  ret_21d: number | null
  ret_63d: number | null
  excess_1m: number | null
  excess_3m: number | null
  turnover_share: number | null
  net_flow_cr: number | null
}
export type TrajectoryCard = {
  sector: string
  rs_trend: string
  rank_path: (number | null)[]
  ranks_per_quarter: number
  participation: number | null
  flow: number | null
  exhaustion: number | null
}
export type SectorHeader = {
  risk_posture: string | null
  cyclical_minus_defensive_pp: number | null
  dispersion_percentile: number | null
  leader_persistence_1m: number | null
  leader_persistence_n: number
  narrative: string
}
export type SectorsResponse = Envelope<SectorRow[]> & {
  header: SectorHeader
  trajectories: { improving: TrajectoryCard[]; deteriorating: TrajectoryCard[] }
}
export type SectorEventRow = {
  date: string
  sector: string
  event: string
  severity: Severity
  description: string
  rank: number
  rs_trend: string
  verdict: string
  historical: HorizonStats & { occurrences?: number }
}

// ---- stocks -----------------------------------------------------------------------------------------------------
export type SearchRow = { symbol: string; date: string; close: number }
export type OhlcvRow = { symbol: string; date: string; open: number; high: number; low: number; close: number; volume: number }
export type FeatureRow = {
  date: string
  close: number
  sma20: number | null
  sma50: number | null
  sma200: number | null
  [k: string]: unknown
}
export type SafetyCheck = { check: string; value: number | null; threshold: string; status: 'PASS' | 'FAIL' | 'UNKNOWN' }
export type PanelData = {
  meta: { symbol: string; company?: string; sector?: string | null; nse_industry?: string; isin?: string }
  price: { close: number; ret_1d: number | null; ret_21d: number | null }
  features: FeatureRow
  lifecycle: {
    state: string | null
    dwell_sessions: number
    dwell_is_lower_bound?: boolean
    since: string | null
    previous_state: string | null
  }
  gauges: { name: string; label: string; value: number | null }[]
  relative_strength: { rs_rank: number | null; rs_500: number | null; benchmark: string }
  safety_stage1: { checks: SafetyCheck[]; overall: 'PASS' | 'FAIL' | 'INCOMPLETE' }
  events: {
    circuit_hits?: { date: string; side: string }[]
    corporate_actions?: { ex_date: string; purpose: string; price_factor: number | null }[]
    news?: PanelNews[]
  }
}

export type ScanRow = { date: string; scan_id: string; symbol: string; state: string; rank: number; score: number }
export type ScanCatalog = { universe: string; scans: { id: string; label: string; state: string; when: string }[] }
export type WatchRow = { user_id: string; symbol: string; created_at: string }

// ---- strategy lab -----------------------------------------------------------------------------------------------
export type RunSummary = {
  run_id: string
  strategy_id: string
  status: string | null
  label: string
  start: string
  end: string
  created_at: string
  gates_passed: number | null
  gates_total: number | null
  gates_all_passed: boolean | null
  review_required: boolean | null
  cagr: number | null
  max_drawdown: number | null
  calmar: number | null
  sharpe: number | null
  trades: number | null
  win_rate: number | null
  pnl: number | null
}
export type StrategyRow = {
  id: string
  name: string
  version: string
  label: string
  family: string
  status: string
  entry: string
  rank_by: string[]
  max_positions: number
  hard_stop_pct: number
  latest_run: RunSummary | null
}
export type JobStatus = { job_id: string; strategy_id: string; status: 'running' | 'done' | 'failed'; run_id: string | null; error: string | null }
export type Metrics = {
  cagr: number | null
  max_drawdown: number | null
  calmar: number | null
  sharpe: number | null
  sortino: number | null
  total_return: number | null
  years: number | null
  trades: number
  win_rate: number | null
  avg_win: number | null
  avg_loss: number | null
  profit_factor: number | null
  pnl: number
  avg_days: number | null
  exposure: number | null
  final_equity: number | null
  by_year: { year: number; return: number | null }[]
  by_regime: { regime: string; trades: number; win_rate: number; avg_ret: number; pnl: number }[]
}
export type Gate = { name: string; passed: boolean | null; value: unknown; threshold: string; detail: string }
export type GateReport = { gates: Gate[]; passed_all: boolean; review_required: boolean; review_reason: string; oos_fraction: number }
export type RunDetail = {
  run: { run_id: string; strategy_id: string; strategy: Record<string, any>; start: string; end: string; sessions: number; duration_s: number }
  metrics: Metrics
  gates: GateReport | null
}
export type EquityData = {
  curve: { date: string; equity: number; cash: number; invested: number; drawdown: number }[]
  benchmark: { date: string; value: number }[]
}
export type TradeRow = {
  trade_id: number
  symbol: string
  sector: string | null
  entry_date: string
  exit_date: string
  days: number
  ret: number
  pnl: number
  exit_reason: string
  regime_at_entry: string | null
  rank: number
}
export type TradeDetail = {
  trade: TradeRow & {
    entry_price: number
    exit_price: number
    shares: number
    mae: number
    mae_day: number
    mfe: number
    mfe_day: number
    peak_price: number
    giveback: number
    initial_stop: number
    final_stop: number
    be_activated: boolean
    selection_reason: string
  }
  window: { date: string; open: number; high: number; low: number; close: number; volume: number; sma20: number | null; sma50: number | null }[]
  regimes: { date: string; regime: string }[]
  explanation: { radar: string; exit: string }
}

// ---- copilot ----------------------------------------------------------------------------------------------------
export type ModelRole = {
  role: string
  position: string
  provider: string
  model: string
  local: boolean
  key_present: boolean
  key_env: string | null
}
export type CopilotModels = { roles: ModelRole[]; policy?: Record<string, boolean>; error?: string }

// ---- alerts, brief, paper trading -------------------------------------------------------------------------------
export type AlertRule = {
  id: number
  name: string
  kind: string
  scan_id: string | null
  min_score: number
  enabled: number
  params: Record<string, unknown>
  created_at: string
}
export type AlertRules = Envelope<AlertRule[]> & { kinds: string[]; channels: string[] }
export type InboxItem = {
  id: number
  key: string
  kind: string
  severity: string
  title: string
  body: string
  as_of: string
  delivered_to: string[]
  errors: string[]
  created_at: string
  read: number
}
export type Inbox = Envelope<InboxItem[]> & { unread: number }
export type Brief = { as_of: string | null; title: string; markdown: string }

export type PaperAccount = {
  id: number
  name: string
  strategy_id: string
  start_date: string
  capital: number
  as_of?: string
  equity?: number
  pnl?: number
  return?: number
  open_positions?: number
  closed_trades?: number
  max_drawdown?: number | null
  error?: string
}
export type OpenPosition = {
  symbol: string
  sector: string
  entry_date: string
  entry_price: number
  shares: number
  last_close: number
  market_value: number
  unrealized_pnl: number
  unrealized_ret: number
  stop: number
  breakeven_active: boolean
  days_held: number
  pending_exit: boolean
}
export type NextOrders = {
  as_of: string
  slots_free: number
  entries: { symbol: string; sector: string; rank: number; indicative_price: number; indicative_shares: number; initial_stop: number }[]
  exits: { symbol: string; reason: string; detail: string }[]
  stops: { symbol: string; stop: number; breakeven: boolean }[]
  note: string
}
export type PaperDetail = {
  account: PaperAccount
  metrics: Metrics
  curve: EquityData['curve']
  open_positions: OpenPosition[]
  next_orders: NextOrders
  trades: TradeRow[]
}
export type LiveSignals = {
  strategy_id: string
  rule: string
  rank_by: string[]
  regime: string | null
  blocked_by_regime: boolean
  note: string
  candidates: { symbol: string; sector: string | null; rank: number; close?: number; ret_21d?: number; ret_63d?: number; rvol20?: number; rsi14?: number; stage?: number }[]
}

// ---- news events ------------------------------------------------------------------------------------------------
export type NewsDirection = 'positive' | 'negative' | 'neutral'

export type PanelNews = {
  event_type: string
  direction: NewsDirection
  published_at: string
  summary: string
  value_cr: number | null
  confidence: number
  url: string | null
}

export type NewsEvent = {
  source_id: string
  symbol: string
  event_type: string
  direction: NewsDirection
  event_date: string
  published_at: string
  available_from: string
  confidence: number
  summary: string
  value_cr: number | null
  pct_of_revenue: number | null
  magnitude_grounded: boolean
  url: string | null
}

export type NewsStudyRow = {
  event_type: string
  direction: NewsDirection
  n: number
  mean_excess_1d: number | null
  mean_excess_5d: number | null
  mean_excess_20d: number | null
  hit_rate_1d: number | null
  hit_rate_5d: number | null
  hit_rate_20d: number | null
  t_stat_1d: number | null
  t_stat_5d: number | null
  t_stat_20d: number | null
}

export type NewsStatus = {
  items: number
  extracted: Record<string, number>
  events_by_type: Record<string, number>
}
