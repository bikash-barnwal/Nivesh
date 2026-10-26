import { type ReactNode, useState } from 'react'

/**
 * How Nivesh works, as an architecture diagram: systems are grouped boxes, arrows say what moves where.
 * Colours: blue = market data, purple = your request, amber = AI, green = your private data, pink = sign-in.
 * Click a box for what it does, where its data lives and how it is protected.
 */

type Flow = 'data' | 'request' | 'ai' | 'private' | 'auth'

const FLOW: Record<Flow, { color: string; label: string }> = {
  data: { color: 'var(--color-info)', label: 'Market data (nightly)' },
  request: { color: 'var(--color-accent)', label: 'Your request' },
  ai: { color: 'var(--color-warn)', label: 'Copilot / AI' },
  private: { color: 'var(--color-good)', label: 'Your private data' },
  auth: { color: '#e879f9', label: 'Sign-in' },
}

type Icon = 'user' | 'shield' | 'globe' | 'server' | 'spark' | 'db' | 'file' | 'key' | 'cloud' | 'gear' | 'chart' | 'check' | 'log'

type Node = {
  id: string
  label: string
  sub: string
  x: number
  y: number
  w?: number
  icon: Icon
  what: string
  where?: string
  guard?: string
}

type Group = { id: string; title: string; x: number; y: number; w: number; h: number; color: string }

type Pt = [number, number]

/** A hand-routed edge: points of a right-angle path, and where its label sits. */
type Edge = { from: string; to: string; label: string; flow: Flow; path: Pt[]; at: Pt; dashed?: boolean }

const H = 58
const W = 180

const GROUPS: Group[] = [
  { id: 'clerk', title: 'Clerk · sign-in service', x: 20, y: 25, w: 230, h: 125, color: FLOW.auth.color },
  { id: 'you', title: 'You', x: 20, y: 255, w: 230, h: 125, color: FLOW.request.color },
  { id: 'server', title: 'Nivesh server', x: 295, y: 25, w: 475, h: 355, color: 'var(--color-muted)' },
  { id: 'gemini', title: 'Google Gemini · your key', x: 1160, y: 25, w: 230, h: 125, color: FLOW.ai.color },
  { id: 'storage', title: 'Data on the server', x: 905, y: 190, w: 485, h: 410, color: 'var(--color-muted)' },
  { id: 'nse', title: 'NSE · public archives', x: 20, y: 695, w: 230, h: 125, color: FLOW.data.color },
  { id: 'pipeline', title: 'Nightly pipeline · weekdays 18:45 IST', x: 295, y: 695, w: 1095, h: 125, color: FLOW.data.color },
]

const NODES: Node[] = [
  {
    id: 'clerk', label: 'Clerk sign-in', sub: 'Google · Hugging Face', x: 45, y: 65, icon: 'shield',
    what: 'You sign in with Google or Hugging Face through Clerk. Nivesh never sees or stores a password. Clerk gives your browser a short-lived signed session token, refreshed about every minute.',
    where: 'Clerk (external service)',
  },
  {
    id: 'browser', label: 'Browser', sub: 'React app', x: 45, y: 295, icon: 'globe',
    what: 'The app you are using. It sends every API call with your session token and shows the answers as charts and tables. The page and "as of" date live in the address bar.',
  },
  {
    id: 'caddy', label: 'HTTPS front door', sub: 'Caddy', x: 320, y: 295, icon: 'server',
    what: 'Serves the app and forwards /api calls to the API, which only listens on the server itself.',
    guard: 'HTTPS only; security headers allow code only from this site and Clerk; 1 MB request limit.',
  },
  {
    id: 'api', label: 'API · access check', sub: 'FastAPI', x: 560, y: 295, icon: 'check',
    what: 'Checks every call: token signed by Clerk, not expired, issued for this site, and your account approved. Then it reads shared market data, or your own rows under your user id only.',
    guard: 'All approved users are equal; asking for another user’s item returns "not found". Per-user limits stop overload.',
  },
  {
    id: 'copilot', label: 'Copilot pipeline', sub: 'SQL guard · number check', x: 560, y: 65, icon: 'spark',
    what: 'Refuses dates after the last loaded session, asks your Gemini model to route the question and write one query, runs that query only after the SQL guard allows it, then checks every number in the written answer against the result.',
    guard: 'Exactly one read-only SELECT over market views; no files, no settings. If a number does not match, you see the raw result.',
  },
  {
    id: 'gemini', label: 'Gemini model', sub: 'the model you picked', x: 1185, y: 65, icon: 'spark',
    what: 'Routes the question, writes the SQL and puts the result into words. It never calculates the numbers you see.',
    where: 'Google, called with your own key',
    guard: 'Receives the question, table descriptions, docs and market query results only; never your account data or key.',
  },
  {
    id: 'duckdb', label: 'DuckDB views', sub: 'read-only · locked', x: 930, y: 230, icon: 'db',
    what: 'One view per Parquet table, in memory. Every page and every Copilot query reads these.',
    guard: 'Can read only the data folder, cannot change its own settings, and every query has a time limit.',
  },
  {
    id: 'parquet', label: 'Parquet tables', sub: 'data/parquet/', x: 1185, y: 230, icon: 'file',
    what: 'Columnar tables: adjusted prices (stock_daily), index history, corporate actions, and the 9 analytics tables (features, breadth, engines, regime, sectors, events, analogues, scans).',
    where: 'data/parquet/<table>/',
  },
  {
    id: 'sqlite', label: 'Your rows', sub: 'app.sqlite3 · per user', x: 930, y: 330, icon: 'db',
    what: 'Watchlist, alert rules and inbox, paper accounts, Copilot history and your settings (AI model). Every row carries your user id; every read and write filters by it.',
    where: 'data/app.sqlite3',
  },
  {
    id: 'keys', label: 'Your API key', sub: 'AES-256-GCM', x: 930, y: 530, icon: 'key',
    what: 'Stored encrypted with a server master key and bound to your user id. Decrypted only for the request that needs it; only the last 4 characters are ever shown.',
    where: 'data/app.sqlite3 → user_llm_keys',
    guard: 'A copied value cannot be decrypted for another user; key text is removed from errors before logging.',
  },
  {
    id: 'runs', label: 'Your backtests', sub: 'data/backtests/', x: 930, y: 430, icon: 'chart',
    what: 'Each strategy run (settings, equity curve, trades) is saved with you as its owner. One run at a time per user.',
  },
  {
    id: 'logs', label: 'Logs & backups', sub: 'data/logs/ · nightly', x: 1185, y: 430, icon: 'log',
    what: 'Daily job log, watchlist audit (with user id), and your Copilot calls in a file of your own (model, time, tokens, never the key). The database is backed up every night and kept 14 days on the server.',
  },
  {
    id: 'raw', label: 'Raw files', sub: 'data/raw/ · as published', x: 1185, y: 330, icon: 'file',
    what: 'Every NSE file exactly as downloaded, so each number can be traced back to its source file.',
    where: 'data/raw/bhav, index, pr, bands, mto, ref_* …',
  },
  {
    id: 'nse', label: 'NSE archives', sub: 'end-of-day files', x: 45, y: 735, icon: 'cloud',
    what: 'The exchange’s official end-of-day files: daily prices for every stock since 2010, index history, corporate actions, price bands, delivery and reference lists. No live or intraday data.',
    where: 'nseindia.com public archives',
  },
  {
    id: 'ingest', label: 'Ingest', sub: 'missing days + last 7', x: 320, y: 735, icon: 'gear',
    what: 'Downloads each missing day and re-checks the last 7 days because NSE sometimes publishes late.',
    guard: 'Polite: about 2.5 requests a second, resumable, never re-downloads a file it has.',
  },
  {
    id: 'build', label: 'Build + adjust', sub: 'splits · bonus · demergers', x: 580, y: 735, icon: 'gear',
    what: 'Parses old and new file formats into one table and adjusts past prices for corporate actions, so charts do not show fake crashes. Every adjustment is recorded.',
  },
  {
    id: 'analytics', label: 'Analytics', sub: '9 steps in order', x: 840, y: 735, icon: 'chart',
    what: 'stock_features → market_breadth → market_engines and market_regime → sector_daily → sector_events → market_events → market_analogues → scan_results. Rules come from versioned YAML through an allow-list.',
    guard: 'A failed step stops the steps that depend on it instead of publishing half-built data.',
  },
  {
    id: 'quality', label: 'Quality checks', sub: 'gaps · outliers · freshness', x: 1100, y: 735, icon: 'check',
    what: 'Checks for missing sessions, impossible prices and stale data, records the last trustworthy date ("Data through"), then refreshes the DuckDB views.',
  },
]

const EDGES: Edge[] = [
  { from: 'nse', to: 'ingest', label: 'CSV / ZIP', flow: 'data', path: [[225, 764], [320, 764]], at: [272, 764] },
  { from: 'ingest', to: 'build', label: 'new days', flow: 'data', path: [[500, 764], [580, 764]], at: [540, 764] },
  { from: 'build', to: 'analytics', label: 'adjusted', flow: 'data', path: [[760, 764], [840, 764]], at: [800, 764] },
  { from: 'analytics', to: 'quality', label: 'tables', flow: 'data', path: [[1020, 764], [1100, 764]], at: [1060, 764] },
  { from: 'ingest', to: 'raw', label: 'files as published', flow: 'data', path: [[410, 735], [410, 655], [1405, 655], [1405, 359], [1365, 359]], at: [560, 655] },
  { from: 'analytics', to: 'parquet', label: 'Parquet tables', flow: 'data', path: [[930, 735], [930, 675], [1390, 675], [1390, 259], [1365, 259]], at: [1000, 675] },
  { from: 'quality', to: 'duckdb', label: 'refresh views', flow: 'data', path: [[1190, 735], [1190, 700], [1150, 700], [1150, 276], [1110, 276]], at: [1150, 620], dashed: true },
  { from: 'parquet', to: 'duckdb', label: 'views', flow: 'data', path: [[1185, 248], [1110, 248]], at: [1148, 220] },
  { from: 'browser', to: 'clerk', label: 'sign in', flow: 'auth', path: [[95, 295], [95, 123]], at: [95, 209] },
  { from: 'clerk', to: 'browser', label: 'session token', flow: 'auth', path: [[195, 123], [195, 295]], at: [195, 209] },
  { from: 'browser', to: 'caddy', label: 'HTTPS + token', flow: 'request', path: [[225, 316], [320, 316]], at: [272, 290] },
  { from: 'caddy', to: 'api', label: '/api', flow: 'request', path: [[500, 324], [560, 324]], at: [530, 324] },
  { from: 'api', to: 'browser', label: 'JSON + source + date', flow: 'request', path: [[650, 353], [650, 430], [135, 430], [135, 353]], at: [390, 430] },
  { from: 'api', to: 'clerk', label: 'verify signature', flow: 'auth', path: [[585, 295], [585, 205], [280, 205], [280, 94], [225, 94]], at: [430, 205], dashed: true },
  { from: 'api', to: 'copilot', label: 'question', flow: 'ai', path: [[635, 295], [635, 123]], at: [635, 165] },
  { from: 'copilot', to: 'api', label: 'answer + evidence', flow: 'ai', path: [[685, 123], [685, 295]], at: [685, 255] },
  { from: 'copilot', to: 'gemini', label: 'question + schema + results', flow: 'ai', path: [[740, 78], [1185, 78]], at: [960, 78] },
  { from: 'gemini', to: 'copilot', label: 'SQL · wording', flow: 'ai', path: [[1185, 112], [740, 112]], at: [960, 112] },
  { from: 'copilot', to: 'duckdb', label: 'guarded SELECT', flow: 'ai', path: [[730, 123], [730, 240], [930, 240]], at: [840, 240] },
  { from: 'api', to: 'duckdb', label: 'read-only SQL', flow: 'request', path: [[740, 308], [790, 308], [790, 274], [930, 274]], at: [862, 274] },
  { from: 'api', to: 'sqlite', label: 'your rows only', flow: 'private', path: [[740, 324], [810, 324], [810, 359], [930, 359]], at: [870, 359] },
  { from: 'api', to: 'runs', label: 'your runs', flow: 'private', path: [[740, 338], [825, 338], [825, 459], [930, 459]], at: [878, 459] },
  { from: 'api', to: 'keys', label: 'decrypt per question', flow: 'private', path: [[740, 349], [840, 349], [840, 559], [930, 559]], at: [840, 510] },
]

const BY_ID = Object.fromEntries(NODES.map((n) => [n.id, n]))
const width = (n: Node) => n.w ?? W

/** SVG path through the points with rounded corners. */
function roundedPath(points: Pt[], r = 10) {
  let d = `M${points[0][0]},${points[0][1]}`
  for (let i = 1; i < points.length - 1; i++) {
    const [px, py] = points[i - 1]
    const [x, y] = points[i]
    const [nx, ny] = points[i + 1]
    const inLen = Math.hypot(x - px, y - py)
    const outLen = Math.hypot(nx - x, ny - y)
    const k = Math.min(r, inLen / 2, outLen / 2)
    const a: Pt = [x - ((x - px) / inLen) * k, y - ((y - py) / inLen) * k]
    const b: Pt = [x + ((nx - x) / outLen) * k, y + ((ny - y) / outLen) * k]
    d += ` L${a[0]},${a[1]} Q${x},${y} ${b[0]},${b[1]}`
  }
  const last = points[points.length - 1]
  return `${d} L${last[0]},${last[1]}`
}

function IconGlyph({ icon, x, y, color }: { icon: Icon; x: number; y: number; color: string }) {
  const p = { fill: 'none', stroke: color, strokeWidth: 1.6, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  const g = (children: ReactNode) => <g transform={`translate(${x},${y})`}>{children}</g>
  switch (icon) {
    case 'user':
      return g(<><circle cx="10" cy="7" r="4" {...p} /><path d="M3 19c1-4 4-6 7-6s6 2 7 6" {...p} /></>)
    case 'shield':
      return g(<><path d="M10 2l7 3v5c0 5-3 8-7 9-4-1-7-4-7-9V5z" {...p} /><path d="M7 10l2 2 4-4" {...p} /></>)
    case 'globe':
      return g(<><circle cx="10" cy="10" r="8" {...p} /><path d="M2 10h16M10 2c3 3 3 13 0 16M10 2c-3 3-3 13 0 16" {...p} /></>)
    case 'server':
      return g(<><rect x="3" y="3" width="14" height="6" rx="1.5" {...p} /><rect x="3" y="11" width="14" height="6" rx="1.5" {...p} /><path d="M6 6h.01M6 14h.01" {...p} /></>)
    case 'spark':
      return g(<path d="M10 2l2 6 6 2-6 2-2 6-2-6-6-2 6-2z" {...p} />)
    case 'db':
      return g(<><ellipse cx="10" cy="5" rx="7" ry="3" {...p} /><path d="M3 5v10c0 1.7 3.1 3 7 3s7-1.3 7-3V5M3 10c0 1.7 3.1 3 7 3s7-1.3 7-3" {...p} /></>)
    case 'file':
      return g(<><path d="M5 2h7l4 4v12H5z" {...p} /><path d="M12 2v4h4M8 11h5M8 14h5" {...p} /></>)
    case 'key':
      return g(<><circle cx="6.5" cy="10" r="3.5" {...p} /><path d="M10 10h8M15 10v3M18 10v2" {...p} /></>)
    case 'cloud':
      return g(<path d="M6 16h9a4 4 0 0 0 0-8 5 5 0 0 0-9.6 1.5A3.3 3.3 0 0 0 6 16z" {...p} />)
    case 'gear':
      return g(<><circle cx="10" cy="10" r="3" {...p} /><path d="M10 2v3M10 15v3M2 10h3M15 10h3M4.3 4.3l2.1 2.1M13.6 13.6l2.1 2.1M4.3 15.7l2.1-2.1M13.6 6.4l2.1-2.1" {...p} /></>)
    case 'chart':
      return g(<path d="M3 17h14M5 14l4-5 3 3 5-7" {...p} />)
    case 'check':
      return g(<><circle cx="10" cy="10" r="8" {...p} /><path d="M6.5 10.5l2.5 2.5 4.5-5" {...p} /></>)
    case 'log':
      return g(<path d="M4 4h12M4 8h12M4 12h8M4 16h10" {...p} />)
  }
}

export function Anatomy() {
  const [selected, setSelected] = useState<string>('api')
  const node = BY_ID[selected] ?? NODES[0]
  const touches = (e: Edge) => e.from === selected || e.to === selected
  const flowsIn = EDGES.filter((e) => e.to === selected)
  const flowsOut = EDGES.filter((e) => e.from === selected)

  return (
    <div className="space-y-4 text-sm">
      <style>{`@keyframes nv-flow { to { stroke-dashoffset: -24; } } .nv-flow { animation: nv-flow 1.2s linear infinite; }
        @media (prefers-reduced-motion: reduce) { .nv-flow { animation: none; } }`}</style>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted">
        {Object.entries(FLOW).map(([k, f]) => (
          <span key={k} className="inline-flex items-center gap-2">
            <svg width="26" height="8" aria-hidden="true"><path d="M1 4h24" stroke={f.color} strokeWidth="2" /></svg>
            {f.label}
          </span>
        ))}
        <span>Click a box for details · dashed = check / signal</span>
      </div>

      <div className="overflow-x-auto rounded border border-line bg-[radial-gradient(var(--color-line)_1px,transparent_1px)] [background-size:18px_18px]">
        <svg viewBox="0 0 1420 840" className="h-auto w-full min-w-[1040px]" role="group" aria-labelledby="anatomy-title">
          <title id="anatomy-title">Nivesh architecture: where data comes from and where it goes</title>
          <defs>
            {Object.entries(FLOW).map(([k, f]) => (
              <marker key={k} id={`arrow-${k}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0 0L10 5L0 10z" fill={f.color} />
              </marker>
            ))}
          </defs>

          {GROUPS.map((g) => (
            <g key={g.id}>
              <rect x={g.x} y={g.y} width={g.w} height={g.h} rx="14" fill="var(--color-panel)" fillOpacity="0.55" stroke={g.color} strokeOpacity="0.55" strokeDasharray="6 5" />
              <text x={g.x + 14} y={g.y + 20} fontSize="12" fontWeight="600" fill={g.color}>
                {g.title}
              </text>
            </g>
          ))}

          {EDGES.map((e) => {
            const d = roundedPath(e.path)
            const on = touches(e)
            const color = FLOW[e.flow].color
            return (
              <g key={`${e.from}-${e.to}`} opacity={on ? 1 : 0.5} style={{ transition: 'opacity 150ms' }}>
                <path d={d} fill="none" stroke={color} strokeWidth={on ? 2.2 : 1.6} strokeDasharray={e.dashed ? '5 5' : undefined} markerEnd={`url(#arrow-${e.flow})`} />
                {!e.dashed && <path d={d} fill="none" stroke={color} strokeWidth={on ? 2.2 : 1.6} strokeDasharray="4 20" className="nv-flow" strokeLinecap="round" opacity="0.9" />}
              </g>
            )
          })}

          {NODES.map((n) => {
            const active = n.id === selected
            const w = width(n)
            return (
              <g
                key={n.id}
                role="button"
                tabIndex={0}
                aria-pressed={active}
                aria-label={`${n.label}: ${n.sub}`}
                onClick={() => setSelected(n.id)}
                onKeyDown={(ev) => (ev.key === 'Enter' || ev.key === ' ') && (ev.preventDefault(), setSelected(n.id))}
                className="cursor-pointer outline-none"
              >
                <rect x={n.x} y={n.y} width={w} height={H} rx="10" fill={active ? 'var(--color-panel-2)' : 'var(--color-panel)'} stroke={active ? 'var(--color-accent)' : 'var(--color-line)'} strokeWidth={active ? 2 : 1} />
                <rect x={n.x + 10} y={n.y + 15} width="28" height="28" rx="7" fill="var(--color-panel-2)" />
                <IconGlyph icon={n.icon} x={n.x + 14} y={n.y + 19} color={active ? 'var(--color-accent)' : 'var(--color-text)'} />
                <text x={n.x + 48} y={n.y + 25} fontSize="13" fontWeight="600" fill="var(--color-text)">
                  {n.label}
                </text>
                <text x={n.x + 48} y={n.y + 42} fontSize="11" fill="var(--color-muted)">
                  {n.sub}
                </text>
              </g>
            )
          })}
          {EDGES.map((e) => {
            const [x, y] = e.at
            const color = FLOW[e.flow].color
            const w = e.label.length * 6.4 + 14
            return (
              <g key={`label-${e.from}-${e.to}`} opacity={touches(e) ? 1 : 0.9} pointerEvents="none">
                <rect x={x - w / 2} y={y - 10} width={w} height="20" rx="10" fill="var(--color-bg)" stroke={color} strokeOpacity="0.8" />
                <text x={x} y={y + 4} fontSize="11" textAnchor="middle" fill="var(--color-text)">
                  {e.label}
                </text>
              </g>
            )
          })}
        </svg>
      </div>

      <article aria-live="polite" className="grid gap-4 rounded border border-line bg-panel p-4 md:grid-cols-[1fr_18rem]">
        <div>
          <h3 className="mb-1 text-base font-semibold text-text">{node.label}</h3>
          <p className="mb-3 text-xs text-muted">{node.sub}</p>
          <p className="mb-3 text-text">{node.what}</p>
          <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-[6rem_1fr]">
            {node.where && (
              <>
                <dt className="text-muted">Where</dt>
                <dd className="font-mono text-xs">{node.where}</dd>
              </>
            )}
            {node.guard && (
              <>
                <dt className="text-muted">Protection</dt>
                <dd>{node.guard}</dd>
              </>
            )}
          </dl>
        </div>
        <div className="space-y-3 text-xs">
          {[['Comes in', flowsIn, 'from'] as const, ['Goes out', flowsOut, 'to'] as const].map(([title, list, end]) =>
            list.length === 0 ? null : (
              <div key={title}>
                <div className="mb-1 font-semibold text-muted">{title}</div>
                <ul className="space-y-1">
                  {list.map((e) => (
                    <li key={`${e.from}-${e.to}`} className="flex items-center gap-2">
                      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: FLOW[e.flow].color }} />
                      <span>
                        {e.label} <span className="text-muted">{end}</span>{' '}
                        <button type="button" onClick={() => setSelected(e[end])} className="text-accent hover:underline">
                          {BY_ID[e[end]].label}
                        </button>
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ),
          )}
        </div>
      </article>
    </div>
  )
}
