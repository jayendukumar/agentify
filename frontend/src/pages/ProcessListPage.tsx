import { useEffect, useState } from 'react'
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
    <div className="page">
      <h2>Processes</h2>

      <form className="create-form" onSubmit={handleCreate}>
        <input
          type="text"
          placeholder="New process name"
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          disabled={user?.role !== 'editor'}
        />
        <button type="submit" disabled={user?.role !== 'editor'} title={user?.role !== 'editor' ? 'Editor access required' : undefined}>
          Create
        </button>
      </form>

      {error && <p className="error">{error}</p>}
      {loading && <p>Loading...</p>}

      {!loading && processes.length === 0 && <p>No processes yet -- create one above.</p>}

      <ul className="process-list">
        {processes.map((process) => (
          <li key={process.id}>
            <Link to={`/processes/${process.id}`}>{process.name}</Link>
            <span className="meta">
              {process.document_count} document{process.document_count === 1 ? '' : 's'}
              {process.has_draft_bpmn ? ' · draft BPMN' : ''}
              {process.finalized_version_count > 0 ? ` · ${process.finalized_version_count} version(s)` : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
