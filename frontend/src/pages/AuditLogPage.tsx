import { useEffect, useState } from 'react'
import { ApiError, auditExportUrl, listAuditEvents } from '../api/client'
import type { AuditEvent, AuditFilters } from '../api/types'
import { useAuth } from '../auth/AuthContext'

// Epic 37, US37.6: the append-only audit trail -- sign-ins, every
// privileged write, approvals and refusals (including segregation-of-duties
// refusals, with their reason).
export default function AuditLogPage() {
  const { can } = useAuth()
  const allowed = can('audit.read')
  const [filters, setFilters] = useState<AuditFilters>({})
  const [applied, setApplied] = useState<AuditFilters>({})
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!allowed) return
    let cancelled = false
    setLoading(true)
    setError(null)
    listAuditEvents(applied)
      .then((rows) => {
        if (!cancelled) setEvents(rows)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load the audit log')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [applied, allowed])

  if (!allowed) {
    return (
      <div className="page">
        <h1>Audit log</h1>
        <p className="meta">The audit log needs the Auditor, Approver or Admin role.</p>
      </div>
    )
  }

  return (
    <div className="page">
      <h1>Audit log</h1>
      <p className="meta">Every sign-in, change, approval and refusal, newest first. Nothing here can be edited or deleted.</p>
      <form
        className="org-upload-form"
        aria-label="Filter audit events"
        onSubmit={(event) => {
          event.preventDefault()
          setApplied(filters)
        }}
      >
        <label>
          Action
          <input type="text" value={filters.action ?? ''} onChange={(e) => setFilters({ ...filters, action: e.target.value })} placeholder="e.g. approve_agent_artifact" />
        </label>
        <label>
          Outcome
          <select value={filters.outcome ?? ''} onChange={(e) => setFilters({ ...filters, outcome: (e.target.value || undefined) as AuditFilters['outcome'] })}>
            <option value="">Any</option>
            <option value="success">Succeeded</option>
            <option value="refused">Refused</option>
          </select>
        </label>
        <label>
          Process id
          <input type="text" value={filters.process_id ?? ''} onChange={(e) => setFilters({ ...filters, process_id: e.target.value })} />
        </label>
        <button type="submit" className="button-secondary">Apply</button>
        <a className="button-secondary" href={auditExportUrl(applied)}>Export CSV</a>
      </form>

      {error && <p className="error" role="alert">{error}</p>}
      {loading && <p className="loading-state" role="status">Loading audit events...</p>}
      {!loading && !error && events.length === 0 && <p className="empty-state">No matching events.</p>}
      {events.length > 0 && (
        <table className="org-register">
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>Action</th>
              <th>On</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {events.map((event) => (
              <tr key={event.id}>
                <td>{new Date(event.at).toLocaleString()}</td>
                <td>{event.actor_name ?? event.actor_type}</td>
                <td>{event.action.replace(/_/g, ' ')}</td>
                <td className="meta">{event.object_type ? `${event.object_type} ${event.object_id ?? ''}` : ''}</td>
                <td>
                  {event.outcome === 'refused' ? (
                    <span className="badge status-failed" title={String(event.detail.reason ?? '')}>Refused</span>
                  ) : (
                    <span className="badge status-done">OK</span>
                  )}
                  {event.outcome === 'refused' && typeof event.detail.reason === 'string' && (
                    <span className="meta"> {event.detail.reason}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
