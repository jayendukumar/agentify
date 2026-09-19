import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { analyzeGaps, ApiError, dismissGapFinding, listGapFindings, resolveGapFinding } from '../api/client'
import type { GapFinding } from '../api/types'

const KIND_LABELS: Record<string, string> = {
  structural: 'Structural',
  cross_document: 'Cross-document',
}

function GapFindingCard({
  finding,
  onResolve,
  onDismiss,
  busy,
}: {
  finding: GapFinding
  onResolve: (findingId: string, optionIndex: number) => void
  onDismiss: (findingId: string) => void
  busy: boolean
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
            <button key={index} type="button" onClick={() => onResolve(finding.id, index)} disabled={busy}>
              {option.label}
            </button>
          ))}
          <button type="button" onClick={() => onDismiss(finding.id)} disabled={busy} className="gap-finding-dismiss">
            Dismiss
          </button>
        </div>
      ) : (
        <div className="gap-finding-decided meta">
          {finding.status === 'resolved' ? `Resolved -- ${finding.chosen_option_label}` : 'Dismissed'}
        </div>
      )}
    </li>
  )
}

export default function GapReviewPage() {
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
      <h2>Gap Review</h2>

      <button type="button" onClick={handleAnalyze} disabled={analyzing}>
        {analyzing ? 'Analyzing...' : 'Re-run analysis'}
      </button>

      {loadError && <p className="error">{loadError}</p>}
      {actionError && <p className="error">{actionError}</p>}
      {loading && <p>Loading...</p>}

      {!loading && findings.length === 0 && (
        <p>No gaps found yet -- upload a document or run analysis to check for issues.</p>
      )}

      {openFindings.length === 0 && !loading && findings.length > 0 && (
        <p className="info">No open gaps -- this process is ready to finalize.</p>
      )}

      <ul className="gap-finding-list">
        {openFindings.map((finding) => (
          <GapFindingCard
            key={finding.id}
            finding={finding}
            onResolve={handleResolve}
            onDismiss={handleDismiss}
            busy={busyFindingId === finding.id}
          />
        ))}
      </ul>

      {decidedFindings.length > 0 && (
        <>
          <button type="button" onClick={() => setShowHistory((v) => !v)} className="gap-history-toggle">
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
                />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
