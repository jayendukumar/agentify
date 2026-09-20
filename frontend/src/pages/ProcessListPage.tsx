import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, createProcess, listProcesses } from '../api/client'
import type { ProcessSummary } from '../api/types'
import { useAuth } from '../auth/AuthContext'

export default function ProcessListPage() {
  const { user } = useAuth()
  const [processes, setProcesses] = useState<ProcessSummary[]>([])
  const [newName, setNewName] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    setLoading(true)
    setError(null)
    try {
      setProcesses(await listProcesses())
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to load processes')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const stats = useMemo(
    () => ({
      total: processes.length,
      inDraft: processes.filter((process) => process.has_draft_bpmn).length,
      finalized: processes.filter((process) => process.finalized_version_count > 0).length,
    }),
    [processes],
  )

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault()
    if (!newName.trim()) return

    setError(null)
    try {
      await createProcess(newName.trim())
      setNewName('')
      await refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create process')
    }
  }

  return (
    <div className="page home-page">
      <section className="home-hero">
        <h2>Welcome back{user ? `, ${user.name}` : ''}</h2>
        <p className="meta">Pick up a process below, or start a new one from source documentation.</p>
      </section>

      <section className="home-stats" aria-label="Process summary">
        <div className="stat-card">
          <span className="stat-value">{stats.total}</span>
          <span className="stat-label">Processes</span>
        </div>
        <div className="stat-card">
          <span className="stat-value">{stats.inDraft}</span>
          <span className="stat-label">In draft</span>
        </div>
        <div className="stat-card">
          <span className="stat-value">{stats.finalized}</span>
          <span className="stat-label">Finalized</span>
        </div>
      </section>

      <section className="home-section">
        <h3>New process</h3>
        <form className="create-form" onSubmit={handleCreate}>
          <input
            type="text"
            placeholder="New process name"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            disabled={user?.role !== 'editor'}
          />
          <button
            type="submit"
            disabled={user?.role !== 'editor'}
            title={user?.role !== 'editor' ? 'Editor access required' : undefined}
          >
            Create
          </button>
        </form>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="home-section">
        <h3>Your processes</h3>

        {loading && <p className="meta">Loading...</p>}
        {!loading && processes.length === 0 && <p className="meta">No processes yet -- create one above.</p>}

        <div className="process-grid">
          {processes.map((process) => (
            <Link to={`/processes/${process.id}`} className="process-card" key={process.id}>
              <span className="process-card-name">{process.name}</span>
              <span className="meta">
                {process.document_count} document{process.document_count === 1 ? '' : 's'}
              </span>
              <span className="process-card-badges">
                {process.has_draft_bpmn && <span className="badge">Draft BPMN</span>}
                {process.finalized_version_count > 0 && (
                  <span className="badge status-done">
                    {process.finalized_version_count} version{process.finalized_version_count === 1 ? '' : 's'}
                  </span>
                )}
              </span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  )
}
