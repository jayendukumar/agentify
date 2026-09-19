import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, diffVersions, listVersions, restoreVersion } from '../api/client'
import type { VersionDiffResult, VersionSummary } from '../api/types'
import { useAuth } from '../auth/AuthContext'

function versionLabel(version: VersionSummary, index: number): string {
  return version.label ?? `Version ${index + 1}`
}

export default function VersionsPage() {
  const { user } = useAuth()
  const { processId } = useParams<{ processId: string }>()
  const [versions, setVersions] = useState<VersionSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [restoringId, setRestoringId] = useState<string | null>(null)
  const [restoreBusyId, setRestoreBusyId] = useState<string | null>(null)
  const [restoreMessage, setRestoreMessage] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const [fromVersionId, setFromVersionId] = useState('')
  const [toVersionId, setToVersionId] = useState('')
  const [diffResult, setDiffResult] = useState<VersionDiffResult | null>(null)
  const [diffing, setDiffing] = useState(false)
  const [diffError, setDiffError] = useState<string | null>(null)

  useEffect(() => {
    if (!processId) return
    let cancelled = false

    setLoading(true)
    setLoadError(null)
    listVersions(processId)
      .then((loaded) => {
        if (cancelled) return
        setVersions(loaded)
        if (loaded.length >= 2) {
          setFromVersionId(loaded[0].id)
          setToVersionId(loaded[loaded.length - 1].id)
        }
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : 'Failed to load versions')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [processId])

  async function handleRestore(versionId: string) {
    if (!processId) return
    setRestoreBusyId(versionId)
    setActionError(null)
    try {
      await restoreVersion(processId, versionId)
      setRestoringId(null)
      setRestoreMessage('Version restored -- open the diagram to see it.')
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to restore version')
    } finally {
      setRestoreBusyId(null)
    }
  }

  async function handleDiff(event: React.FormEvent) {
    event.preventDefault()
    if (!processId || !fromVersionId || !toVersionId) return
    setDiffing(true)
    setDiffError(null)
    setDiffResult(null)
    try {
      setDiffResult(await diffVersions(processId, fromVersionId, toVersionId))
    } catch (err) {
      setDiffError(err instanceof ApiError ? err.message : 'Failed to compare versions')
    } finally {
      setDiffing(false)
    }
  }

  if (!processId) return <p>Missing process id.</p>

  return (
    <div className="page">
      <p>
        <Link to={`/processes/${processId}/diagram`}>&larr; Back to diagram</Link>
      </p>
      <h2>Version History</h2>

      {loadError && <p className="error">{loadError}</p>}
      {loading && <p>Loading...</p>}
      {restoreMessage && <p className="info">{restoreMessage}</p>}
      {actionError && <p className="error">{actionError}</p>}

      {!loading && versions.length === 0 && (
        <p>No finalized versions yet -- finalize a draft from the diagram page.</p>
      )}

      <ul className="version-list">
        {versions.map((version, index) => (
          <li key={version.id} className="version-row">
            <span>{versionLabel(version, index)}</span>
            <span className="meta">
              {new Date(version.created_at).toLocaleString()}
              {version.created_by_name ? ` · finalized by ${version.created_by_name}` : ''}
            </span>
            <span className="version-row-actions">
              {restoringId === version.id ? (
                <>
                  <span className="meta">Restore this version? This replaces the current draft.</span>
                  <button
                    type="button"
                    onClick={() => handleRestore(version.id)}
                    disabled={restoreBusyId === version.id}
                  >
                    {restoreBusyId === version.id ? 'Restoring...' : 'Confirm restore'}
                  </button>
                  <button type="button" onClick={() => setRestoringId(null)} disabled={restoreBusyId === version.id}>
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => setRestoringId(version.id)}
                  disabled={user?.role !== 'editor'}
                  title={user?.role !== 'editor' ? 'Editor access required' : undefined}
                >
                  Restore
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>

      {versions.length >= 2 && (
        <>
          <h3>Compare versions</h3>
          <form className="version-diff-form" onSubmit={handleDiff}>
            <select
              value={fromVersionId}
              onChange={(event) => setFromVersionId(event.target.value)}
              aria-label="From version"
            >
              {versions.map((v, i) => (
                <option key={v.id} value={v.id}>
                  {versionLabel(v, i)}
                </option>
              ))}
            </select>
            <span>&rarr;</span>
            <select
              value={toVersionId}
              onChange={(event) => setToVersionId(event.target.value)}
              aria-label="To version"
            >
              {versions.map((v, i) => (
                <option key={v.id} value={v.id}>
                  {versionLabel(v, i)}
                </option>
              ))}
            </select>
            <button type="submit" disabled={diffing}>
              {diffing ? 'Comparing...' : 'Compare'}
            </button>
          </form>

          {diffError && <p className="error">{diffError}</p>}

          {diffResult && (
            <div className="version-diff">
              <div className="version-diff-column">
                <h4>Added</h4>
                {diffResult.added_element_ids.length === 0 && <p className="meta">None</p>}
                <ul>
                  {diffResult.added_element_ids.map((id) => (
                    <li key={id}>{diffResult.labels[id] ?? id}</li>
                  ))}
                </ul>
              </div>
              <div className="version-diff-column">
                <h4>Removed</h4>
                {diffResult.removed_element_ids.length === 0 && <p className="meta">None</p>}
                <ul>
                  {diffResult.removed_element_ids.map((id) => (
                    <li key={id}>{diffResult.labels[id] ?? id}</li>
                  ))}
                </ul>
              </div>
              <div className="version-diff-column">
                <h4>Changed</h4>
                {diffResult.changed_element_ids.length === 0 && <p className="meta">None</p>}
                <ul>
                  {diffResult.changed_element_ids.map((id) => (
                    <li key={id}>{diffResult.labels[id] ?? id}</li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
