import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, getCurrentUser, listProcesses, onUnauthorized } from './client'

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
