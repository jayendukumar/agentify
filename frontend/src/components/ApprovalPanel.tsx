import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ApiError } from '../api/client'
import type { ApprovalStatus } from '../api/types'

// Epic 37, US37.3-US37.5: one pending change set -- who changed what
// (high-risk changes first), who has approved, and either the approve
// button or the plain reason the signed-in user can't approve it.
export default function ApprovalPanel({
  load,
  onApprove,
  onReject,
  refreshKey,
}: {
  load: () => Promise<ApprovalStatus>
  onApprove: (comment: string) => Promise<void>
  onReject?: (comment: string) => Promise<void>
  refreshKey?: unknown
}) {
  const [status, setStatus] = useState<ApprovalStatus | null>(null)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Callers pass an inline `load`; keep the latest in a ref so a re-render
  // doesn't refetch (and loop) -- `refreshKey` is the explicit trigger.
  const loadRef = useRef(load)
  useLayoutEffect(() => {
    loadRef.current = load
  })
  const refresh = useCallback(() => {
    loadRef.current()
      .then(setStatus)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load approval status'))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh, refreshKey])

  async function decide(action: (comment: string) => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await action(comment)
      setComment('')
      refresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to record the decision')
    } finally {
      setBusy(false)
    }
  }

  if (!status) return error ? <p className="error" role="alert">{error}</p> : null

  const high = status.changes.filter((c) => c.tier === 'high')
  const other = status.changes.filter((c) => c.tier !== 'high')
  const pending = status.status === 'pending_approval'

  return (
    <div className="approval-panel" data-testid="approval-panel">
      {pending && (
        <p className="meta">
          {status.approvals.length} of {status.required_approvals} approval{status.required_approvals === 1 ? '' : 's'}
          {status.approvals.length > 0 && ` (${status.approvals.map((a) => a.approver_name ?? 'unknown').join(', ')})`}
        </p>
      )}
      {high.length > 0 && (
        <>
          <p className="meta"><strong>Changes needing independent approval</strong></p>
          <ul className="approval-changes">
            {high.map((c, i) => (
              <li key={`h${i}`} className="meta">
                {c.actor_name ?? 'System'} {c.label}{c.at ? ` · ${new Date(c.at).toLocaleString()}` : ''}
              </li>
            ))}
          </ul>
        </>
      )}
      {other.length > 0 && (
        <details>
          <summary className="meta">Other activity ({other.length})</summary>
          <ul className="approval-changes">
            {other.map((c, i) => (
              <li key={`s${i}`} className="meta">
                {c.actor_name ?? 'System'} {c.label}{c.at ? ` · ${new Date(c.at).toLocaleString()}` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
      {pending && status.can_approve && (
        <div className="registry-entry-actions">
          <input
            type="text"
            aria-label="Approval comment"
            placeholder="Comment (optional)"
            value={comment}
            disabled={busy}
            onChange={(event) => setComment(event.target.value)}
          />
          <button type="button" className="button-primary" disabled={busy} onClick={() => decide(onApprove)}>
            Approve
          </button>
          {onReject && (
            <button type="button" className="button-secondary" disabled={busy} onClick={() => decide(onReject)}>
              Reject
            </button>
          )}
        </div>
      )}
      {pending && !status.can_approve && status.reason && (
        <p className="meta" role="note">{status.reason}</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}
