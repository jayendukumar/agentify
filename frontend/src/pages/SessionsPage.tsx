import { useEffect, useState } from 'react'
import { ApiError, endMySession, listMySessions } from '../api/client'
import type { SessionInfo } from '../api/types'
import SessionList from '../components/SessionList'

// Epic 37, US37.13: where you're signed in. Ending another session signs
// that browser out on its next request; this browser signs out with
// "Log out" in the header.
export default function SessionsPage() {
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listMySessions()
      .then(setSessions)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load your sessions'))
  }, [])

  async function end(session: SessionInfo) {
    setError(null)
    try {
      await endMySession(session.id)
      setSessions((current) => current?.filter((s) => s.id !== session.id) ?? null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not end that session')
    }
  }

  return (
    <div className="page">
      <h1>Your sessions</h1>
      <p className="meta">
        Every browser you're signed in on. A session ends after a period without activity, or at the latest at the time
        shown. If you don't recognize one, end it.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {sessions === null ? <p className="loading-state" role="status">Loading...</p> : <SessionList sessions={sessions} onEnd={end} endLabel="End session" />}
    </div>
  )
}
