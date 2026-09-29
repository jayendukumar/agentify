import { useState } from 'react'
import { ApiError, approveAgentArtifact, markAgentReleaseCandidate } from '../api/client'
import type { AgentArtifact, LifecycleStatus } from '../api/types'

const LIFECYCLE_LABELS: Record<LifecycleStatus, string> = {
  DRAFT: 'Draft',
  VALIDATED: 'Validated',
  SIMULATION_READY: 'Simulation ready',
  SIMULATING: 'Simulating',
  EVALUATED: 'Evaluated',
  RELEASE_CANDIDATE: 'Release candidate',
  APPROVED: 'Approved',
  DEPLOYED: 'Deployed',
}

function badgeClass(status: LifecycleStatus): string {
  if (status === 'DEPLOYED' || status === 'APPROVED') return 'badge status-done'
  if (status === 'DRAFT') return 'badge status-failed'
  return 'badge status-processing'
}

// Epic 23: the DRAFT..DEPLOYED quality gate -- the mandatory validation
// checklist (US23.2) made visible, the computed lifecycle status (US23.1),
// and the two explicit human actions (mark release candidate / approve,
// US23.4) that are the only states nothing can derive. This is the
// "readiness" evidence US23.4 asks the publish flow to read, rather than
// PublishPanel deriving its own separate notion of ready.
export default function LifecyclePanel({
  processId,
  artifact,
  canManage,
}: {
  processId: string
  artifact: AgentArtifact
  canManage: boolean
}) {
  const [current, setCurrent] = useState(artifact)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleMarkReleaseCandidate() {
    setError(null)
    setBusy(true)
    try {
      setCurrent(await markAgentReleaseCandidate(processId, artifact.id))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to mark as a release candidate')
    } finally {
      setBusy(false)
    }
  }

  async function handleApprove() {
    setError(null)
    setBusy(true)
    try {
      setCurrent(await approveAgentArtifact(processId, artifact.id))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to approve')
    } finally {
      setBusy(false)
    }
  }

  const unsatisfied = current.checklist.filter((item) => !item.satisfied)

  return (
    <div className="lifecycle-panel" data-testid="lifecycle-panel">
      <p className="element-meta">
        <span className={badgeClass(current.lifecycle_status)}>{LIFECYCLE_LABELS[current.lifecycle_status]}</span>
      </p>
      {unsatisfied.length > 0 && (
        <ul className="lifecycle-checklist">
          {unsatisfied.map((item) => (
            <li key={item.key} className="meta">
              Missing: {item.label}
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <div className="registry-entry-actions">
          <button
            type="button"
            className="button-secondary"
            onClick={handleMarkReleaseCandidate}
            disabled={busy || current.lifecycle_status === 'DRAFT' || current.marked_release_candidate_at !== null}
          >
            Mark release candidate
          </button>
          <button
            type="button"
            className="button-secondary"
            onClick={handleApprove}
            disabled={busy || current.approved_at !== null}
          >
            Approve
          </button>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}
