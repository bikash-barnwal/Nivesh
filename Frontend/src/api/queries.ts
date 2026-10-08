import { queryOptions } from '@tanstack/react-query'
import { type Envelope, get } from './client'
import type {
  AlertRules,
  AnalogueResponse,
  Brief,
  CopilotModels,
  EngineCatalog,
  EngineRow,
  EquityData,
  EventRow,
  EventsResponse,
  FeatureRow,
  Freshness,
  Inbox,
  JobStatus,
  LiveSignals,
  NewsEvent,
  NewsStatus,
  NewsStudyRow,
  OhlcvRow,
  OverviewData,
  PaperAccount,
  PaperDetail,
  PanelData,
  RegimeRow,
  RunDetail,
  RunSummary,
  ScanCatalog,
  ScanRow,
  SearchRow,
  SectorEventRow,
  SectorsResponse,
  StrategyRow,
  TradeDetail,
  TradeRow,
  WatchRow,
} from './types'

/** History is immutable once published; "latest" views refresh when the EOD job lands. */
const stale = (asOf?: string) => (asOf ? Number.POSITIVE_INFINITY : 60_000)
const key = (asOf?: string) => asOf ?? 'latest'

export const q = {
  freshness: () =>
    queryOptions({
      queryKey: ['freshness'],
      queryFn: ({ signal }) => get<Freshness>('/api/data/freshness', undefined, signal),
      staleTime: 30_000,
    }),

  overview: (asOf?: string) =>
    queryOptions({
      queryKey: ['market', 'overview', key(asOf)],
      queryFn: ({ signal }) => get<Envelope<OverviewData>>('/api/market/overview', { as_of: asOf }, signal),
      staleTime: stale(asOf),
    }),

  engines: (asOf?: string) =>
    queryOptions({
      queryKey: ['market', 'engines', key(asOf)],
      queryFn: ({ signal }) => get<Envelope<EngineRow[]>>('/api/market/engines', { as_of: asOf, sparkline: 60 }, signal),
      staleTime: stale(asOf),
    }),

  engineCatalog: () =>
    queryOptions({
      queryKey: ['catalog', 'engines'],
      queryFn: ({ signal }) => get<Envelope<EngineCatalog>>('/api/catalog/engines', undefined, signal),
      staleTime: Number.POSITIVE_INFINITY,
    }),

  regimeHistory: (asOf?: string, sessions = 150) =>
    queryOptions({
      queryKey: ['market', 'regime-history', key(asOf), sessions],
      queryFn: ({ signal }) =>
        get<Envelope<RegimeRow[]>>('/api/market/regime-history', { as_of: asOf, sessions }, signal),
      staleTime: stale(asOf),
    }),

  events: (asOf?: string) =>
    queryOptions({
      queryKey: ['market', 'events', key(asOf)],
      queryFn: ({ signal }) => get<EventsResponse>('/api/market/events', { as_of: asOf }, signal),
      staleTime: stale(asOf),
    }),

  eventLog: (asOf?: string) =>
    queryOptions({
      queryKey: ['market', 'event-log', key(asOf)],
      queryFn: ({ signal }) =>
        get<Envelope<(Omit<EventRow, 'outcomes' | 'recent_occurrences' | 'actions'>)[]>>(
          '/api/market/events/log',
          { as_of: asOf, sessions: 30 },
          signal,
        ),
      staleTime: stale(asOf),
    }),

  analogues: (asOf?: string) =>
    queryOptions({
      queryKey: ['market', 'analogues', key(asOf)],
      queryFn: ({ signal }) => get<AnalogueResponse>('/api/market/analogues', { as_of: asOf }, signal),
      staleTime: stale(asOf),
    }),

  sectors: (asOf?: string) =>
    queryOptions({
      queryKey: ['sectors', key(asOf)],
      queryFn: ({ signal }) => get<SectorsResponse>('/api/sectors', { as_of: asOf }, signal),
      staleTime: stale(asOf),
    }),

  sectorEvents: (asOf?: string) =>
    queryOptions({
      queryKey: ['sectors', 'events', key(asOf)],
      queryFn: ({ signal }) =>
        get<Envelope<SectorEventRow[]>>('/api/sectors/events', { as_of: asOf, sessions: 10 }, signal),
      staleTime: stale(asOf),
    }),

  scanCatalog: () =>
    queryOptions({
      queryKey: ['scans', 'catalog'],
      queryFn: ({ signal }) => get<Envelope<ScanCatalog>>('/api/scan-catalog', undefined, signal),
      staleTime: Number.POSITIVE_INFINITY,
    }),

  scan: (scanId: string, asOf?: string, top = 50) =>
    queryOptions({
      queryKey: ['scans', scanId, key(asOf), top],
      queryFn: ({ signal }) => get<Envelope<ScanRow[]>>(`/api/scans/${scanId}`, { as_of: asOf, top }, signal),
      staleTime: stale(asOf),
    }),

  stockSearch: (term: string) =>
    queryOptions({
      queryKey: ['stocks', 'search', term],
      queryFn: ({ signal }) => get<Envelope<SearchRow[]>>('/api/stocks/search', { q: term, limit: 8 }, signal),
      staleTime: 60_000,
      enabled: term.trim().length > 0,
    }),

  panel: (symbol: string, asOf?: string) =>
    queryOptions({
      queryKey: ['stocks', symbol, 'panel', key(asOf)],
      queryFn: ({ signal }) => get<Envelope<PanelData>>(`/api/stocks/${symbol}/panel`, { as_of: asOf }, signal),
      staleTime: stale(asOf),
    }),

  ohlcv: (symbol: string, asOf?: string, limit = 500) =>
    queryOptions({
      queryKey: ['stocks', symbol, 'ohlcv', key(asOf), limit],
      queryFn: ({ signal }) =>
        get<Envelope<OhlcvRow[]>>(`/api/stocks/${symbol}/ohlcv`, { as_of: asOf, limit }, signal),
      staleTime: stale(asOf),
    }),

  features: (symbol: string, asOf?: string, limit = 500) =>
    queryOptions({
      queryKey: ['stocks', symbol, 'features', key(asOf), limit],
      queryFn: ({ signal }) =>
        get<Envelope<FeatureRow[]>>(`/api/stocks/${symbol}/features`, { as_of: asOf, limit }, signal),
      staleTime: stale(asOf),
    }),

  copilotModels: () =>
    queryOptions({
      queryKey: ['copilot', 'models'],
      queryFn: ({ signal }) => get<Envelope<CopilotModels>>('/api/copilot/models', undefined, signal),
      staleTime: 60_000,
    }),

  watchlist: () =>
    queryOptions({
      queryKey: ['watchlist'],
      queryFn: ({ signal }) => get<Envelope<WatchRow[]>>('/api/watchlist', undefined, signal),
      staleTime: 0,
    }),
}

export const lab = {
  strategies: () =>
    queryOptions({
      queryKey: ['lab', 'strategies'],
      queryFn: ({ signal }) => get<Envelope<StrategyRow[]>>('/api/strategies', undefined, signal),
      staleTime: 0,
    }),
  leaderboard: () =>
    queryOptions({
      queryKey: ['lab', 'leaderboard'],
      queryFn: ({ signal }) => get<Envelope<RunSummary[]>>('/api/leaderboard', undefined, signal),
      staleTime: 0,
    }),
  job: (jobId: string) =>
    queryOptions({
      queryKey: ['lab', 'job', jobId],
      queryFn: ({ signal }) => get<Envelope<JobStatus>>(`/api/backtests/jobs/${jobId}`, undefined, signal),
      refetchInterval: (query) => (query.state.data?.data.status === 'running' ? 1000 : false),
    }),
  run: (runId: string) =>
    queryOptions({
      queryKey: ['lab', 'run', runId],
      queryFn: ({ signal }) => get<Envelope<RunDetail>>(`/api/backtests/${runId}`, undefined, signal),
      staleTime: Number.POSITIVE_INFINITY,
    }),
  equity: (runId: string) =>
    queryOptions({
      queryKey: ['lab', 'equity', runId],
      queryFn: ({ signal }) => get<Envelope<EquityData>>(`/api/backtests/${runId}/equity`, undefined, signal),
      staleTime: Number.POSITIVE_INFINITY,
    }),
  trades: (runId: string, filters: { year?: number; exit_reason?: string; sort: 'date' | 'pnl' }) =>
    queryOptions({
      queryKey: ['lab', 'trades', runId, filters],
      queryFn: ({ signal }) =>
        get<Envelope<TradeRow[]> & { total: number }>(
          `/api/backtests/${runId}/trades`,
          { ...filters, order: 'desc', limit: 2000 },
          signal,
        ),
      staleTime: Number.POSITIVE_INFINITY,
    }),
  trade: (runId: string, tradeId: number) =>
    queryOptions({
      queryKey: ['lab', 'trade', runId, tradeId],
      queryFn: ({ signal }) => get<Envelope<TradeDetail>>(`/api/backtests/${runId}/trades/${tradeId}`, undefined, signal),
      staleTime: Number.POSITIVE_INFINITY,
    }),
}

export const ops = {
  brief: (asOf?: string) =>
    queryOptions({
      queryKey: ['brief', asOf ?? 'latest'],
      queryFn: ({ signal }) => get<Envelope<Brief>>('/api/brief/text', { as_of: asOf }, signal),
      staleTime: asOf ? Number.POSITIVE_INFINITY : 60_000,
    }),
  alertRules: () =>
    queryOptions({
      queryKey: ['alerts', 'rules'],
      queryFn: ({ signal }) => get<AlertRules>('/api/alerts', undefined, signal),
      staleTime: 0,
    }),
  inbox: () =>
    queryOptions({
      queryKey: ['alerts', 'inbox'],
      queryFn: ({ signal }) => get<Inbox>('/api/alerts/inbox', { limit: 100 }, signal),
      staleTime: 0,
    }),
  paperAccounts: () =>
    queryOptions({
      queryKey: ['paper', 'list'],
      queryFn: ({ signal }) => get<Envelope<PaperAccount[]>>('/api/paper', undefined, signal),
      staleTime: 0,
    }),
  paper: (id: number) =>
    queryOptions({
      queryKey: ['paper', id],
      queryFn: ({ signal }) => get<Envelope<PaperDetail>>(`/api/paper/${id}`, undefined, signal),
      staleTime: 30_000,
    }),
  liveSignals: (strategyId: string) =>
    queryOptions({
      queryKey: ['signals', strategyId],
      queryFn: ({ signal }) => get<Envelope<LiveSignals>>('/api/signals/live', { strategy_id: strategyId, limit: 25 }, signal),
      staleTime: 60_000,
      enabled: strategyId !== '',
    }),
}

export const news = {
  events: (asOf?: string, limit = 100) =>
    queryOptions({
      queryKey: ['news', 'events', key(asOf), limit],
      queryFn: ({ signal }) => get<Envelope<NewsEvent[]>>('/api/news/events', { as_of: asOf, limit }, signal),
      staleTime: stale(asOf),
    }),
  study: () =>
    queryOptions({
      queryKey: ['news', 'study'],
      queryFn: ({ signal }) => get<Envelope<NewsStudyRow[]>>('/api/news/study', undefined, signal),
      staleTime: 60_000,
    }),
  status: () =>
    queryOptions({
      queryKey: ['news', 'status'],
      queryFn: ({ signal }) => get<Envelope<NewsStatus>>('/api/news/status', undefined, signal),
      staleTime: 60_000,
    }),
}
