import { useState } from 'react'
import { ApiError } from '../api/client'
import type { Role } from '../api/types'
import { useAuth } from './AuthContext'

export default function LoginPage() {
  const { login } = useAuth()
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await login(name.trim(), role)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to log in')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page login-page">
      <h2>Agentic Solution Generator</h2>
      <p className="meta">
        Enter your name to continue. This is a lightweight, local-team login (no password) -- the role you pick only
        applies the first time this name is used; later logins reuse whatever role was set then.
      </p>
      <form onSubmit={handleSubmit} className="login-form">
        <label>
          Name
          <input
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Alex"
            autoFocus
          />
        </label>
        <label>
          Role (first login only)
          <select value={role} onChange={(event) => setRole(event.target.value as Role)}>
            <option value="viewer">Viewer -- can view everything, cannot edit or finalize</option>
            <option value="editor">Editor -- can upload, edit, finalize, and override</option>
          </select>
        </label>
        {error && <p className="error">{error}</p>}
        <button type="submit" disabled={submitting || !name.trim()}>
          {submitting ? 'Logging in...' : 'Continue'}
        </button>
      </form>
    </div>
  )
}
