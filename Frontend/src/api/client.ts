/** Standard envelope every intelligence endpoint returns. `extra` endpoint fields are added by intersection. */
export type Envelope<T> = {
  as_of: string | null
  data_through: string | null
  version: number
  data: T
  provenance: { source: string; calculation: string; [k: string]: unknown }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Params = Record<string, string | number | boolean | undefined | null>

function url(path: string, params?: Params): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `${path}?${qs}` : path
}

async function parse<T>(res: Response): Promise<T> {
  if (res.ok) return (await res.json()) as T
  let detail = res.statusText
  try {
    const body = (await res.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') detail = body.detail
  } catch {
    /* non-JSON error body */
  }
  throw new ApiError(res.status, detail)
}

export async function get<T>(path: string, params?: Params, signal?: AbortSignal): Promise<T> {
  return parse<T>(await fetch(url(path, params), { signal }))
}

export async function send<T>(method: 'POST' | 'DELETE', path: string, params?: Params): Promise<T> {
  return parse<T>(await fetch(url(path, params), { method }))
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  return parse<T>(
    await fetch(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }),
  )
}
