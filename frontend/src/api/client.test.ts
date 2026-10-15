import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createProcess, getCurrentUser, listProcesses, login, onUnauthorized } from './client'

describe('client 401 handling', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  beforeEach(() => {
    onUnauthorized(() => {})
  })

  it('notifies the registered listener when any endpoint returns 401', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Session expired or invalid -- log in again' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const listener = vi.fn()
    onUnauthorized(listener)

    await expect(listProcesses()).rejects.toBeInstanceOf(ApiError)
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('does not notify the listener on a successful response', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([]), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    const listener = vi.fn()
    onUnauthorized(listener)

    await listProcesses()
    expect(listener).not.toHaveBeenCalled()
  })

  it('getCurrentUser still resolves to null on 401 instead of throwing', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'Not logged in' }), { status: 401 }))

    await expect(getCurrentUser()).resolves.toBeNull()
  })
})

// Epic 37, US37.12: 15-minute session cookies, a refresh cookie, and a
// CSRF token on every write.
describe('client session refresh and CSRF', () => {
  const originalFetch = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })
  }

  const signedIn = (csrf: string) => ({
    id: 'u1', name: 'Ana', active: true, roles: [], permissions: [], created_at: '',
    csrf_token: csrf, session_id: 's1', session_expires_at: '', idle_timeout_minutes: 30,
  })

  function paths(fetchMock: ReturnType<typeof vi.fn>): string[] {
    return fetchMock.mock.calls.map(([url]) => new URL(String(url)).pathname)
  }

  it('refreshes once on 401 and retries the request', async () => {
    let processCalls = 0
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith('/api/auth/refresh')) return json(signedIn('t1'))
      processCalls += 1
      return processCalls === 1 ? json({ detail: 'expired' }, 401) : json([])
    })
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const listener = vi.fn()
    onUnauthorized(listener)

    await expect(listProcesses()).resolves.toEqual([])
    expect(paths(fetchMock)).toEqual(['/api/processes', '/api/auth/refresh', '/api/processes'])
    expect(listener).not.toHaveBeenCalled()
  })

  it('shares one refresh between requests that 401 together', async () => {
    let refreshed = false
    const fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith('/api/auth/refresh')) {
        await new Promise((resolve) => setTimeout(resolve, 10))
        refreshed = true
        return json(signedIn('t1'))
      }
      return refreshed ? json([]) : json({ detail: 'expired' }, 401)
    })
    globalThis.fetch = fetchMock as unknown as typeof fetch

    await Promise.all([listProcesses(), listProcesses(), listProcesses()])
    expect(paths(fetchMock).filter((p) => p === '/api/auth/refresh')).toHaveLength(1)
  })

  it('signs out when the refresh is refused', async () => {
    const fetchMock = vi.fn(async () => json({ detail: 'Session expired or invalid -- log in again' }, 401))
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const listener = vi.fn()
    onUnauthorized(listener)

    await expect(listProcesses()).rejects.toBeInstanceOf(ApiError)
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('sends the CSRF token from sign-in on writes, not on reads', async () => {
    const fetchMock = vi.fn(async (url: string, _init?: RequestInit) =>
      url.endsWith('/api/auth/login') ? json(signedIn('from-login')) : json(url.endsWith('/api/processes') ? [] : {}),
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch

    await login('Ana', ['analyst'])
    await listProcesses()
    await createProcess('P')
    const header = (i: number) => new Headers(fetchMock.mock.calls[i][1]?.headers).get('X-CSRF-Token')
    expect(header(1)).toBeNull()
    expect(header(2)).toBe('from-login')
  })

  it('fetches a fresh CSRF token and retries when the server says it is stale', async () => {
    let posts = 0
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/api/auth/me')) return json(signedIn('fresh'))
      posts += 1
      const token = new Headers(init?.headers).get('X-CSRF-Token')
      return token === 'fresh' ? json({ id: 'p1' }, 201) : json({ detail: 'stale' }, 403, { 'X-Auth-Error': 'csrf' })
    })
    globalThis.fetch = fetchMock as unknown as typeof fetch

    await expect(createProcess('P')).resolves.toEqual({ id: 'p1' })
    expect(posts).toBe(2)
  })
})
