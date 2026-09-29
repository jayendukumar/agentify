import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  createRegressionSuite,
  deleteRegressionSuite,
  getRegressionSuiteRun,
  listRegressionSuites,
  startRegressionSuiteRun,
} from '../api/client'
import type { RegressionSuite, RegressionSuiteRun } from '../api/types'

const POLL_INTERVAL_MS = 2000

// Epic 19, US19.2/US19.3: a named, reusable set of scenarios re-run
// together as one regression pass -- reused by both DigitalTwinPanel
// (kind="twin", one agent's scenarios) and OrchestrationPanel
// (kind="orchestration", process-wide rehearsal scenarios), since the
// aggregate create/run/poll flow is identical for both.
export default function RegressionSuitePanel({
  processId,
  kind,
  agentArtifactId,
  availableScenarios,
}: {
  processId: string
  kind: 'twin' | 'orchestration'
  agentArtifactId: string | null
  availableScenarios: { id: string; name: string }[]
}) {
  const [suites, setSuites] = useState<RegressionSuite[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [selectedScenarioIds, setSelectedScenarioIds] = useState<string[]>([])
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const [runsBySuite, setRunsBySuite] = useState<Record<string, RegressionSuiteRun>>({})
  const [startingId, setStartingId] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listRegressionSuites(processId)
      .then((list) => {
        if (!cancelled) setSuites(list.filter((s) => s.kind === kind))
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : 'Failed to load regression suites')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [processId, kind])

  useEffect(() => {
    return () => {
      Object.values(pollTimers.current).forEach(clearInterval)
    }
  }, [])

  function startPolling(suiteId: string, suiteRunId: string) {
    const existing = pollTimers.current[suiteId]
    if (existing) clearInterval(existing)
    const timer = setInterval(async () => {
      try {
        const run = await getRegressionSuiteRun(processId, suiteRunId)
        setRunsBySuite((prev) => ({ ...prev, [suiteId]: run }))
        if (run.status === 'completed') {
          clearInterval(timer)
          delete pollTimers.current[suiteId]
        }
      } catch (err) {
        clearInterval(timer)
        delete pollTimers.current[suiteId]
        setRunError(err instanceof ApiError ? err.message : 'Lost connection while watching the suite run')
      }
    }, POLL_INTERVAL_MS)
    pollTimers.current[suiteId] = timer
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault()
    setCreateError(null)
    try {
      setCreating(true)
      const suite = await createRegressionSuite(processId, {
        name,
        kind,
        agent_artifact_id: agentArtifactId,
        scenario_ids: selectedScenarioIds,
      })
      setSuites((prev) => [...prev, suite])
      setName('')
      setSelectedScenarioIds([])
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Failed to create suite')
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(suiteId: string) {
    if (!window.confirm('Delete this regression suite? This cannot be undone.')) return
    try {
      await deleteRegressionSuite(processId, suiteId)
      setSuites((prev) => prev.filter((s) => s.id !== suiteId))
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : 'Could not delete the suite. Please try again.')
    }
  }

  async function handleRun(suiteId: string) {
    setStartingId(suiteId)
    setRunError(null)
    try {
      const suiteRun = await startRegressionSuiteRun(processId, suiteId)
      setRunsBySuite((prev) => ({ ...prev, [suiteId]: suiteRun }))
      if (suiteRun.status !== 'completed') startPolling(suiteId, suiteRun.id)
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : 'Failed to start the suite run')
    } finally {
      setStartingId(null)
    }
  }

  function toggleScenario(id: string) {
    setSelectedScenarioIds((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]))
  }

  return (
    <div className="regression-suite-panel" data-testid="regression-suite-panel">
      <h3>Regression suites</h3>
      {loadError && <p className="error" role="alert">{loadError}</p>}
      {loading ? (
        <p>Loading...</p>
      ) : suites.length === 0 ? (
        <p className="meta">No regression suites defined yet.</p>
      ) : (
        <ul className="registry-entry-list">
          {suites.map((suite) => {
            const run = runsBySuite[suite.id]
            return (
              <li key={suite.id} className="registry-entry-card" data-testid="regression-suite-card">
                <div className="registry-entry-header">
                  <span>{suite.name}</span>
                  <span className="meta">{suite.scenario_ids.length} scenario(s)</span>
                </div>
                <div className="registry-entry-actions">
                  <button type="button" onClick={() => handleRun(suite.id)} disabled={startingId === suite.id}>
                    {startingId === suite.id ? 'Starting...' : 'Run suite'}
                  </button>
                  <button type="button" className="button-secondary" onClick={() => handleDelete(suite.id)}>
                    Delete
                  </button>
                </div>
                {run && (
                  <p className="meta" data-testid="regression-suite-result">
                    {run.status === 'completed'
                      ? `${run.pass_count}/${run.total_count} passed`
                      : `Running -- ${run.scenario_results.length}/${run.run_ids.length} finished so far`}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {runError && <p className="error" role="alert">{runError}</p>}

      <h4>New suite</h4>
      <form className="twin-scenario-form" onSubmit={handleCreate}>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <fieldset>
          <legend>Scenarios</legend>
          {availableScenarios.length === 0 && <p className="meta">No scenarios defined yet to add to a suite.</p>}
          {availableScenarios.map((scenario) => (
            <label key={scenario.id} className="checkbox-label">
              <input
                type="checkbox"
                checked={selectedScenarioIds.includes(scenario.id)}
                onChange={() => toggleScenario(scenario.id)}
              />
              {scenario.name}
            </label>
          ))}
        </fieldset>
        <button type="submit" disabled={creating || selectedScenarioIds.length === 0}>
          {creating ? 'Creating...' : 'Create suite'}
        </button>
        {createError && <p className="error" role="alert">{createError}</p>}
      </form>
    </div>
  )
}
