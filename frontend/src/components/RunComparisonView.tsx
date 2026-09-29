import { useState } from 'react'
import { ApiError, compareRuns } from '../api/client'
import type { RunComparison } from '../api/types'

// Epic 19, US19.4: side-by-side version comparison -- picks two runs (
// typically from two different agent versions, US19.5) and shows the
// product's own called-out behavioral differences rather than two raw
// traces the viewer has to diff by eye.
export default function RunComparisonView({
  processId,
  runs,
}: {
  processId: string
  runs: { id: string; label: string }[]
}) {
  const [runAId, setRunAId] = useState('')
  const [runBId, setRunBId] = useState('')
  const [comparison, setComparison] = useState<RunComparison | null>(null)
  const [comparing, setComparing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleCompare() {
    if (!runAId || !runBId) return
    setError(null)
    setComparing(true)
    try {
      setComparison(await compareRuns(processId, runAId, runBId))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to compare these runs')
    } finally {
      setComparing(false)
    }
  }

  if (runs.length < 2) {
    return <p className="meta">Run this scenario at least twice to compare results.</p>
  }

  return (
    <div className="run-comparison-view" data-testid="run-comparison-view">
      <div className="run-comparison-grid">
        <label>
          Run A
          <select value={runAId} onChange={(e) => setRunAId(e.target.value)}>
            <option value="">Select a run</option>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Run B
          <select value={runBId} onChange={(e) => setRunBId(e.target.value)}>
            <option value="">Select a run</option>
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <button type="button" onClick={handleCompare} disabled={!runAId || !runBId || comparing}>
        {comparing ? 'Comparing...' : 'Compare runs'}
      </button>
      {error && <p className="error" role="alert">{error}</p>}
      {comparison && (
        <ul data-testid="run-comparison-differences">
          {comparison.differences.map((diff, i) => (
            <li key={i} className="meta">
              {diff}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
