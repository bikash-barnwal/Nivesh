// Clerk session token for API calls. Unset when Clerk is off (local dev without a key), so requests go out unsigned.
let tokenGetter: (() => Promise<string | null>) | null = null

export function setTokenGetter(fn: (() => Promise<string | null>) | null) {
  tokenGetter = fn
}

export async function authFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = tokenGetter ? await tokenGetter() : null
  if (!token) return fetch(input, init)
  const headers = new Headers(init.headers)
  headers.set('authorization', `Bearer ${token}`)
  return fetch(input, { ...init, headers })
}
