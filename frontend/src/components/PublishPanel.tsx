import { useCallback, useEffect, useState } from 'react'
import { ApiError, getPublishStatus, listRegistries, markPublicationDeployed, publishAgentArtifact } from '../api/client'
import type { AgentPublishStatus, RegistryStatus } from '../api/types'
import TwinConfidenceBadge from './TwinConfidenceBadge'

const LIFECYCLE_LABELS: Record<string, string> = {
  generated: 'not published',
  published: 'published',
  deployed: 'deployed',
}

// Epic 15: the polished, status-tracked, access-controlled "Publish"
// action promised by RegistryPushAction's old docstring -- built on the
// same /api/processes/.../publish endpoint (itself a thin wrapper around
// Epic 13's /api/registries/{name}/push), plus US15.2's lifecycle status,
// US15.3's twin evidence, and US15.4's version history. Bundles
// TwinConfidenceBadge here (rather than each caller rendering it
// separately) so twin results are always shown right on the publish
// decision, never a gate on it (US14.6/US15.3).
export default function PublishPanel({
  processId,
  artifactId,
  canPublish,
}: {
  processId: string
  artifactId: string
  canPublish: boolean
}) {
  const [registries, setRegistries] = useState<RegistryStatus[]>([])
  const [selected, setSelected] = useState('')
  const [status, setStatus] = useState<AgentPublishStatus | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [markingDeployedId, setMarkingDeployedId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshStatus = useCallback(() => {
    getPublishStatus(processId, artifactId)
      .then(setStatus)
      .catch(() => {
        // Best-effort, same degraded-state convention as TwinConfidenceBadge.
      })
  }, [processId, artifactId])

  useEffect(() => {
    listRegistries()
      .then((list) => {
        setRegistries(list)
        setSelected((current) => current || list[0]?.name || '')
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    setError(null)
    refreshStatus()
  }, [artifactId, refreshStatus])

  async function handlePublish() {
    if (!selected) return
    setPublishing(true)
    setError(null)
    try {
      await publishAgentArtifact(processId, artifactId, selected)
      refreshStatus()
    } catch (err) {
      // US15.5: a failed publish never touches `status` -- the last known
      // lifecycle stays on screen alongside this retryable error.
      setError(err instanceof ApiError ? err.message : 'Failed to publish -- try again')
    } finally {
      setPublishing(false)
    }
  }

  async function handleMarkDeployed(publicationId: string) {
    setMarkingDeployedId(publicationId)
    setError(null)
    try {
      await markPublicationDeployed(processId, artifactId, publicationId)
      refreshStatus()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to mark as deployed')
    } finally {
      setMarkingDeployedId(null)
    }
  }

  const lifecycle = status?.lifecycle_status ?? 'generated'
  const latest = status?.latest_publication ?? null

  return (
    <div className="publish-panel">
      <TwinConfidenceBadge processId={processId} artifactId={artifactId} />

      <div className="element-meta">
        <strong>Publish status:</strong>{' '}
        <span className={`badge ${lifecycle === 'deployed' ? 'status-done' : lifecycle === 'published' ? 'confidence-medium' : ''}`}>
          {LIFECYCLE_LABELS[lifecycle]}
        </span>
        {status?.needs_republish && (
          <span className="badge confidence-medium" title="The artifact was regenerated since the latest publish">
            republish needed
          </span>
        )}
      </div>

      {registries.length > 0 && (
        <div className="registry-push-action">
          <label>
            Registry
            <select value={selected} onChange={(event) => setSelected(event.target.value)}>
              {registries.map((registry) => (
                <option key={registry.name} value={registry.name} disabled={!registry.reachable}>
                  {registry.name}
                  {registry.reachable ? '' : ' (unreachable)'}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={handlePublish}
            disabled={publishing || !selected || !canPublish}
            title={!canPublish ? 'Editor access required' : undefined}
          >
            {publishing ? 'Publishing...' : latest ? 'Publish new version' : 'Publish'}
          </button>
        </div>
      )}

      {error && <p className="error">{error}</p>}

      {latest && (
        <p className="info">
          v{latest.version} pushed to &ldquo;{latest.registry_name}&rdquo;
          {latest.published_by_name ? ` by ${latest.published_by_name}` : ''}.{' '}
          {latest.status !== 'deployed' && canPublish && (
            <button
              type="button"
              className="button-secondary"
              onClick={() => handleMarkDeployed(latest.id)}
              disabled={markingDeployedId === latest.id}
            >
              {markingDeployedId === latest.id ? 'Marking...' : 'Mark deployed'}
            </button>
          )}
        </p>
      )}

      {status && status.publications.length > 1 && (
        <details className="publish-history">
          <summary>Version history ({status.publications.length})</summary>
          <ul>
            {status.publications.map((publication) => (
              <li key={publication.id}>
                v{publication.version} &middot; {publication.registry_name} &middot;{' '}
                {new Date(publication.published_at).toLocaleString()} &middot; {publication.status}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
