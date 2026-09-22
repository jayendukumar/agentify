import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { analyzeGaps, ApiError, dismissGapFinding, listGapFindings, resolveGapFinding } from '../api/client'
import type { GapFinding } from '../api/types'
import { useAuth } from '../auth/AuthContext'

const KIND_LABELS: Record<string, string> = {
  structural: 'Structural',
  cross_document: 'Cross-document',
}

function GapFindingCard({
  finding,
  onResolve,
  onDismiss,
  busy,
  canDecide,
}: {
  finding: GapFinding
  onResolve: (findingId: string, optionIndex: number) => void
  onDismiss: (findingId: string) => void
  busy: boolean
  canDecide: boolean
}) {
  return (
    <li className="gap-finding-card">
      <div className="gap-finding-header">
        <span className="badge">{KIND_LABELS[finding.kind] ?? finding.kind}</span>
        <span className="gap-finding-question">{finding.question}</span>
      </div>
      {finding.status === 'open' ? (
        <div className="gap-finding-actions">
          {finding.options.map((option, index) => (
            <button
              key={index}
              type="button"
              onClick={() => onResolve(finding.id, index)}
              disabled={busy || !canDecide}
              title={!canDecide ? 'Editor access required' : undefined}
            >
              {option.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => onDismiss(finding.id)}
            disabled={busy || !canDecide}
            title={!canDecide ? 'Editor access required' : undefined}
            className="gap-finding-dismiss"
          >
            Dismiss
          </button>
        </div>
      ) : (
        <div className="gap-finding-decided meta">
          {finding.status === 'resolved' ? `Resolved -- ${finding.chosen_option_label}` : 'Dismissed'}
          {finding.decided_by_name ? ` (${finding.decided_by_name})` : ''}
        </div>
      )}
    </li>
  )
}

export default function GapReviewPage() {
  const { user } = useAuth()
  const isEditor = user?.role === 'editor'
  const { processId } = useParams<{ processId: string }>()
  const [findings, setFindings] = useState<GapFinding[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [busyFindingId, setBusyFindingId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(false)

  const load = () => {
    if (!processId) return
    setLoading(true)
    setLoadError(null)
    listGapFindings(processId)
      .then(setFindings)
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : 'Failed to load gap findings'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [processId])

  async function handleAnalyze() {
    if (!processId) return
    setAnalyzing(true)
    setActionError(null)
    try {
      setFindings(await analyzeGaps(processId))
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to run gap analysis')
    } finally {
      setAnalyzing(false)
    }
  }

  async function handleResolve(findingId: string, optionIndex: number) {
    if (!processId) return
    setBusyFindingId(findingId)
    setActionError(null)
    try {
      await resolveGapFinding(processId, findingId, optionIndex)
      load()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to resolve gap finding')
    } finally {
      setBusyFindingId(null)
    }
  }

  async function handleDismiss(findingId: string) {
    if (!processId) return
    setBusyFindingId(findingId)
    setActionError(null)
    try {
      await dismissGapFinding(processId, findingId)
      load()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to dismiss gap finding')
    } finally {
      setBusyFindingId(null)
    }
  }

  if (!processId) return <p>Missing process id.</p>

  const openFindings = findings.filter((f) => f.status === 'open')
  const decidedFindings = findings.filter((f) => f.status !== 'open')

  return (
    <div className="page">
      <p>
        <Link to={`/processes/${processId}/diagram`}>&larr; Back to diagram</Link>
      </p>
      <h1>Gap Review</h1>
      <p className="meta">Resolve missing or ambiguous process details before finalizing your baseline.</p>

      <button
        type="button"
        onClick={handleAnalyze}
        className="button-primary"
        disabled={analyzing || !isEditor}
        title={!isEditor ? 'Editor access required' : undefined}
      >
        {analyzing ? 'Analyzing...' : 'Re-run analysis'}
      </button>

      {loadError && <p className="error" role="alert">{loadError}</p>}
      {actionError && <p className="error" role="alert">{actionError}</p>}
      {loading && <p className="loading-state" role="status">Loading gaps...</p>}

      {!loading && !loadError && findings.length === 0 && (
        <p className="empty-state">No gaps found yet -- upload a document or run analysis to check for issues.</p>
      )}

      {openFindings.length === 0 && !loading && findings.length > 0 && (
        <p className="info" role="status">No open gaps -- this process is ready to finalize.</p>
      )}

      <ul className="gap-finding-list">
        {openFindings.map((finding) => (
          <GapFindingCard
            key={finding.id}
            finding={finding}
            onResolve={handleResolve}
            onDismiss={handleDismiss}
            busy={busyFindingId === finding.id}
            canDecide={isEditor}
          />
        ))}
      </ul>

      {decidedFindings.length > 0 && (
        <>
          <button type="button" aria-expanded={showHistory} onClick={() => setShowHistory((v) => !v)} className="gap-history-toggle">
            {showHistory ? 'Hide' : 'Show'} history ({decidedFindings.length})
          </button>
          {showHistory && (
            <ul className="gap-finding-list">
              {decidedFindings.map((finding) => (
                <GapFindingCard
                  key={finding.id}
                  finding={finding}
                  onResolve={handleResolve}
                  onDismiss={handleDismiss}
                  busy={false}
                  canDecide={isEditor}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
