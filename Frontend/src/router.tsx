import { QueryClient } from '@tanstack/react-query'
import { createRootRouteWithContext, createRoute, createRouter, Link, redirect } from '@tanstack/react-router'
import { lab, news, q } from './api/queries'
import { Shell } from './components/Shell'
import { isIsoDate } from './lib/format'
import { AlertsPage } from './pages/AlertsPage'
import { CopilotPage } from './pages/CopilotPage'
import { LabPage } from './pages/LabPage'
import { MarketPage } from './pages/MarketPage'
import { NewsPage } from './pages/NewsPage'
import { PaperDetailPage, PaperPage } from './pages/PaperPage'
import { RunPage } from './pages/RunPage'
import { ScansPage } from './pages/ScansPage'
import { SectorsPage } from './pages/SectorsPage'
import { StockPage } from './pages/StockPage'
import { WatchlistPage } from './pages/WatchlistPage'

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
})

/** Every view can be time-travelled: `as_of` is validated once here and inherited by all routes. */
type RootSearch = { as_of?: string }

const rootRoute = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: Shell,
  validateSearch: (search: Record<string, unknown>): RootSearch => ({
    as_of: isIsoDate(search.as_of) ? search.as_of : undefined,
  }),
  notFoundComponent: () => (
    <p className="text-muted">
      Page not found.{' '}
      <Link to="/market" className="text-accent hover:underline">
        Back to the market view
      </Link>
    </p>
  ),
})

const keepAsOf = (prev: RootSearch): RootSearch => ({ as_of: prev.as_of })

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/market', search: keepAsOf })
  },
})

const marketRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/market',
  loaderDeps: ({ search }) => ({ asOf: search.as_of }),
  loader: ({ context, deps }) => {
    // Warm the cache; failures (e.g. analytics not built yet) are rendered by the page, not thrown here.
    void Promise.allSettled([
      context.queryClient.prefetchQuery(q.overview(deps.asOf)),
      context.queryClient.prefetchQuery(q.engines(deps.asOf)),
      context.queryClient.prefetchQuery(q.events(deps.asOf)),
      context.queryClient.prefetchQuery(q.analogues(deps.asOf)),
    ])
  },
  component: MarketPage,
})

const sectorsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/sectors',
  loaderDeps: ({ search }) => ({ asOf: search.as_of }),
  loader: ({ context, deps }) => {
    void Promise.allSettled([
      context.queryClient.prefetchQuery(q.sectors(deps.asOf)),
      context.queryClient.prefetchQuery(q.sectorEvents(deps.asOf)),
    ])
  },
  component: SectorsPage,
})

const stocksIndexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/stocks',
  beforeLoad: () => {
    throw redirect({ to: '/stocks/$symbol', params: { symbol: 'RELIANCE' }, search: keepAsOf })
  },
})

const stockRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/stocks/$symbol',
  loaderDeps: ({ search }) => ({ asOf: search.as_of }),
  loader: ({ context, deps, params }) => {
    const symbol = params.symbol.toUpperCase()
    void Promise.allSettled([
      context.queryClient.prefetchQuery(q.panel(symbol, deps.asOf)),
      context.queryClient.prefetchQuery(q.ohlcv(symbol, deps.asOf, 500)),
      context.queryClient.prefetchQuery(q.features(symbol, deps.asOf, 500)),
    ])
  },
  component: function StockRoute() {
    const { symbol } = stockRoute.useParams()
    return <StockPage symbol={symbol} />
  },
})

const scansIndexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/scans',
  beforeLoad: () => {
    throw redirect({ to: '/scans/$scanId', params: { scanId: 'early_entry' }, search: keepAsOf })
  },
})

const scanRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/scans/$scanId',
  loaderDeps: ({ search }) => ({ asOf: search.as_of }),
  loader: ({ context, deps, params }) => {
    void Promise.allSettled([
      context.queryClient.prefetchQuery(q.scanCatalog()),
      context.queryClient.prefetchQuery(q.scan(params.scanId, deps.asOf, 100)),
    ])
  },
  component: function ScanRoute() {
    const { scanId } = scanRoute.useParams()
    return <ScansPage scanId={scanId} />
  },
})

const labRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/strategies',
  loader: ({ context }) => {
    void context.queryClient.prefetchQuery(lab.strategies())
  },
  component: LabPage,
})

const runRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/runs/$runId',
  loader: ({ context, params }) => {
    void Promise.allSettled([
      context.queryClient.prefetchQuery(lab.run(params.runId)),
      context.queryClient.prefetchQuery(lab.equity(params.runId)),
    ])
  },
  component: function RunRoute() {
    const { runId } = runRoute.useParams()
    return <RunPage runId={runId} />
  },
})

const newsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/news',
  loaderDeps: ({ search }) => ({ asOf: search.as_of }),
  loader: ({ context, deps }) => {
    void Promise.allSettled([
      context.queryClient.prefetchQuery(news.status()),
      context.queryClient.prefetchQuery(news.study()),
      context.queryClient.prefetchQuery(news.events(deps.asOf)),
    ])
  },
  component: NewsPage,
})

const copilotRoute = createRoute({ getParentRoute: () => rootRoute, path: '/copilot', component: CopilotPage })

const alertsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/alerts', component: AlertsPage })

const paperRoute = createRoute({ getParentRoute: () => rootRoute, path: '/paper', component: PaperPage })

const paperDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/paper/$accountId',
  component: function PaperDetailRoute() {
    const { accountId } = paperDetailRoute.useParams()
    return <PaperDetailPage accountId={Number(accountId)} />
  },
})

const watchlistRoute = createRoute({ getParentRoute: () => rootRoute, path: '/watchlist', component: WatchlistPage })

const routeTree = rootRoute.addChildren([
  indexRoute,
  marketRoute,
  sectorsRoute,
  stocksIndexRoute,
  stockRoute,
  scansIndexRoute,
  scanRoute,
  labRoute,
  runRoute,
  newsRoute,
  copilotRoute,
  alertsRoute,
  paperRoute,
  paperDetailRoute,
  watchlistRoute,
])

export const router = createRouter({
  routeTree,
  context: { queryClient },
  defaultPreload: 'intent',
  defaultPreloadStaleTime: 0,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}
