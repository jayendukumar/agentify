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
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [createdName, setCreatedName] = useState<string | null>(null)

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
    if (!newName.trim() || creating) return

    setCreating(true)
    setCreateError(null)
    setCreatedName(null)
    try {
      await createProcess(newName.trim())
      setCreatedName(newName.trim())
      setNewName('')
      await refresh()
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : 'Failed to create process. Please try again.')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="page home-page">
      <section className="home-hero">
        <p className="eyebrow">Your workspace</p>
        <h1>Welcome back{user ? `, ${user.name}` : ''}</h1>
        <p className="home-tagline">Discover processes. Activate intelligence.</p>
        <p className="meta">Pick up a process below, or start a new one from source documentation.</p>
      </section>

      <section className="home-stats" aria-label="Process summary">
        <div className="stat-card">
          <span className="stat-value">{loading ? '—' : stats.total}</span>
          <span className="stat-label">Processes</span>
        </div>
        <div className="stat-card">
          <span className="stat-value">{loading ? '—' : stats.inDraft}</span>
          <span className="stat-label">In draft</span>
        </div>
        <div className="stat-card">
          <span className="stat-value">{loading ? '—' : stats.finalized}</span>
          <span className="stat-label">Finalized</span>
        </div>
      </section>

      <section className="home-section">
        <h2>New process</h2>
        {user?.role !== 'editor' && <p className="meta">You have Viewer access. An Editor can create processes and upload documents.</p>}
        <form className="create-form" onSubmit={handleCreate}>
          <label>
            Process name
          <input
            type="text"
            placeholder="New process name"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            disabled={creating || user?.role !== 'editor'}
            required
            aria-describedby={createError ? 'create-error' : undefined}
          />
          </label>
          <button
            type="submit"
            disabled={creating || !newName.trim() || user?.role !== 'editor'}
            title={user?.role !== 'editor' ? 'Editor access required' : undefined}
          >
            {creating ? 'Creating...' : 'Create process'}
          </button>
        </form>
        {createError && <p id="create-error" className="error" role="alert">{createError}</p>}
        {createdName && <p className="success" role="status">“{createdName}” created. Open it below to upload documents.</p>}
      </section>

      <section className="home-section">
        <h2>Your processes</h2>

        {loading && <p className="loading-state" role="status">Loading processes...</p>}
        {error && <div><p className="error" role="alert">{error}</p><button type="button" onClick={refresh}>Try again</button></div>}
        {!loading && !error && processes.length === 0 && <p className="empty-state">{user?.role === 'editor' ? 'No processes yet. Create your first process above, then upload its source documents.' : 'No processes yet. An Editor can add the first process.'}</p>}

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
              <span className="process-card-action">Open process <span aria-hidden="true">→</span></span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  )
}
