import { useEffect, useState } from 'react'
import { ApiError, getTwinSummary } from '../api/client'
import type { TwinSummary } from '../api/types'

// US14.6: surfaces Epic 14's twin evidence on Epic 8's blueprint view --
// read-only and informational only, never a gate on generating or
// publishing an agent (see the epic's Notes on why: twin evidence should
// inform, not block, that decision).
export default function TwinConfidenceBadge({ processId, artifactId }: { processId: string; artifactId: string }) {
  const [summary, setSummary] = useState<TwinSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getTwinSummary(processId, artifactId)
      .then((result) => {
        if (!cancelled) setSummary(result)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : 'Failed to load twin results')
      })
    return () => {
      cancelled = true
    }
  }, [processId, artifactId])

  if (error) return null
  if (!summary || summary.run_count === 0) {
    return (
      <div className="element-meta" data-testid="twin-confidence-badge">
        <strong>Digital twin:</strong> <span className="meta">not tested yet</span>
      </div>
    )
  }

  const passClass = summary.pass_rate === 1 ? 'status-done' : summary.pass_rate === 0 ? 'status-failed' : ''
  return (
    <div className="element-meta" data-testid="twin-confidence-badge">
      <strong>Digital twin:</strong>{' '}
      <span className={`badge ${passClass}`}>
        {summary.pass_rate !== null ? `${Math.round(summary.pass_rate * 100)}% pass` : 'n/a'}
      </span>{' '}
      <span className="meta">
        ({summary.run_count} run{summary.run_count === 1 ? '' : 's'})
      </span>
    </div>
  )
}
