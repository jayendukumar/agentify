import { useEffect, useMemo, useState } from 'react'
import {
  ApiError,
  createScenario,
  deleteScenario,
  deleteTwinBaseline,
  getTwinSummary,
  listScenarios,
  listTwinRuns,
  runScenario,
  setTwinBaseline,
} from '../api/client'
import type {
  AgentArtifact,
  TwinExpectedStep,
  TwinHumanCheckpointConfig,
  TwinRun,
  TwinScenario,
  TwinSummary,
  TwinSystemStub,
} from '../api/types'
import type { AgentGroup } from '../lib/blueprintLabels'

const DEFAULT_HUMAN_CHECKPOINT_CONFIG = '{"mode": "probability", "approve_probability": 1.0}'

function statusBadgeClass(status: TwinRun['status']): string {
  return status === 'passed' ? 'badge status-done' : 'badge status-failed'
}

function parseJsonField<T>(raw: string, fieldLabel: string): T {
  try {
    return JSON.parse(raw) as T
  } catch {
    throw new Error(`${fieldLabel} is not valid JSON`)
  }
}

export default function DigitalTwinPanel({ processId, groups }: { processId: string; groups: AgentGroup[] }) {
  const artifacts = useMemo(
    () => groups.map((g) => g.artifact).filter((a): a is AgentArtifact => a !== null),
    [groups],
  )
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(artifacts[0]?.id ?? null)
  useEffect(() => {
    if (!selectedArtifactId && artifacts.length > 0) setSelectedArtifactId(artifacts[0].id)
  }, [artifacts, selectedArtifactId])

  const [scenarios, setScenarios] = useState<TwinScenario[]>([])
  const [runsByScenario, setRunsByScenario] = useState<Record<string, TwinRun>>({})
  const [summary, setSummary] = useState<TwinSummary | null>(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [runningId, setRunningId] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [inputsJson, setInputsJson] = useState('{}')
  const [systemStubsJson, setSystemStubsJson] = useState('{}')
  const [checkpointJson, setCheckpointJson] = useState(DEFAULT_HUMAN_CHECKPOINT_CONFIG)
  const [expectedStepsJson, setExpectedStepsJson] = useState('[]')
  const [expectedOutputsJson, setExpectedOutputsJson] = useState('{}')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const [baselineTimeInput, setBaselineTimeInput] = useState('')
  const [baselineErrorPercentInput, setBaselineErrorPercentInput] = useState('')
  const [baselineNotes, setBaselineNotes] = useState('')
  const [savingBaseline, setSavingBaseline] = useState(false)
  const [baselineError, setBaselineError] = useState<string | null>(null)

  useEffect(() => {
    if (!selectedArtifactId) {
      setScenarios([])
      setSummary(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setLoadError(null)

    Promise.all([
      listScenarios(processId, selectedArtifactId),
      listTwinRuns(processId, selectedArtifactId),
      getTwinSummary(processId, selectedArtifactId),
    ])
      .then(([scenarioList, runList, twinSummary]) => {
        if (cancelled) return
        setScenarios(scenarioList)
        setSummary(twinSummary)
        setBaselineTimeInput(twinSummary.baseline?.typical_time_seconds?.toString() ?? '')
        setBaselineErrorPercentInput(
          twinSummary.baseline?.error_rate != null ? (twinSummary.baseline.error_rate * 100).toString() : '',
        )
        setBaselineNotes(twinSummary.baseline?.notes ?? '')
        // listTwinRuns returns most-recent-first (see repository.list_twin_runs) --
        // keep only the first (latest) run seen per scenario.
        const latestByScenario: Record<string, TwinRun> = {}
        for (const run of runList) {
          if (!latestByScenario[run.scenario_id]) latestByScenario[run.scenario_id] = run
        }
        setRunsByScenario(latestByScenario)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : 'Failed to load scenarios')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [processId, selectedArtifactId])

  async function refreshSummary(artifactId: string) {
    setSummary(await getTwinSummary(processId, artifactId))
  }

  async function handleCreateScenario(event: React.FormEvent) {
    event.preventDefault()
    if (!selectedArtifactId) return
    setCreateError(null)
    try {
      const body = {
        name,
        inputs: parseJsonField<Record<string, unknown>>(inputsJson, 'Inputs'),
        system_stubs: parseJsonField<Record<string, TwinSystemStub>>(systemStubsJson, 'System stubs'),
        human_checkpoint_config: parseJsonField<TwinHumanCheckpointConfig>(checkpointJson, 'Human checkpoint config'),
        expected_steps: parseJsonField<TwinExpectedStep[]>(expectedStepsJson, 'Expected steps'),
        expected_outputs: parseJsonField<Record<string, unknown>>(expectedOutputsJson, 'Expected outputs'),
      }
      setCreating(true)
      const scenario = await createScenario(processId, selectedArtifactId, body)
      setScenarios((prev) => [...prev, scenario])
      setName('')
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Failed to create scenario')
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(scenarioId: string) {
    await deleteScenario(processId, scenarioId)
    setScenarios((prev) => prev.filter((s) => s.id !== scenarioId))
  }

  async function handleSaveBaseline(event: React.FormEvent) {
    event.preventDefault()
    if (!selectedArtifactId) return
    setBaselineError(null)
    try {
      const errorPercent = baselineErrorPercentInput.trim() === '' ? null : Number(baselineErrorPercentInput)
      if (errorPercent !== null && (Number.isNaN(errorPercent) || errorPercent < 0 || errorPercent > 100)) {
        throw new Error('Error rate must be a percentage between 0 and 100')
      }
      setSavingBaseline(true)
      const baseline = await setTwinBaseline(processId, selectedArtifactId, {
        typical_time_seconds: baselineTimeInput.trim() === '' ? null : Number(baselineTimeInput),
        error_rate: errorPercent === null ? null : errorPercent / 100,
        notes: baselineNotes.trim() === '' ? null : baselineNotes.trim(),
      })
      setSummary((prev) => (prev ? { ...prev, baseline } : prev))
      await refreshSummary(selectedArtifactId)
    } catch (err) {
      setBaselineError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Failed to save baseline')
    } finally {
      setSavingBaseline(false)
    }
  }

  async function handleClearBaseline() {
    if (!selectedArtifactId) return
    await deleteTwinBaseline(processId, selectedArtifactId)
    setBaselineTimeInput('')
    setBaselineErrorPercentInput('')
    setBaselineNotes('')
    await refreshSummary(selectedArtifactId)
  }

  async function handleRun(scenarioId: string) {
    if (!selectedArtifactId) return
    setRunningId(scenarioId)
    setRunError(null)
    try {
      const run = await runScenario(processId, scenarioId)
      setRunsByScenario((prev) => ({ ...prev, [scenarioId]: run }))
      await refreshSummary(selectedArtifactId)
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : 'Failed to run scenario')
    } finally {
      setRunningId(null)
    }
  }

  if (artifacts.length === 0) {
    return (
      <div className="page" data-testid="digital-twin-panel">
        <p className="meta">
          No generated agent artifacts yet -- generate an agent from the Agents tab before defining twin scenarios.
        </p>
      </div>
    )
  }

  return (
    <div className="page" data-testid="digital-twin-panel">
      <div className="create-form" role="tablist" aria-label="Select agent artifact">
        {artifacts.map((artifact) => (
          <button
            key={artifact.id}
            type="button"
            role="tab"
            aria-selected={artifact.id === selectedArtifactId}
            className={`tab-button${artifact.id === selectedArtifactId ? ' tab-button-active' : ''}`}
            onClick={() => setSelectedArtifactId(artifact.id)}
          >
            {artifact.definition.name}
          </button>
        ))}
      </div>

      {loadError && <p className="error">{loadError}</p>}

      {summary && (
        <div className="registry-entry-card" data-testid="twin-summary-card">
          <div className="registry-entry-header">
            <span>Twin summary</span>
            <span className="meta">{summary.run_count} run(s)</span>
          </div>
          {summary.run_count > 0 && (
            <>
              <p className="meta">
                Pass rate: {summary.pass_rate !== null ? `${Math.round(summary.pass_rate * 100)}%` : 'n/a'} -- Total
                cost: {summary.total_cost_usd !== null ? `$${summary.total_cost_usd.toFixed(4)}` : 'unknown'}
              </p>
              {summary.common_failure_reasons.length > 0 && (
                <ul>
                  {summary.common_failure_reasons.map((reason) => (
                    <li key={reason} className="meta">
                      {reason}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}

          <h4>As-is baseline (manual estimate)</h4>
          <p className="meta">
            Optional and user-supplied -- nothing in this system measures the as-is step automatically.
          </p>
          <form className="twin-scenario-form" onSubmit={handleSaveBaseline} data-testid="twin-baseline-form">
            <label>
              Typical time to complete (seconds)
              <input
                type="number"
                min={0}
                value={baselineTimeInput}
                onChange={(e) => setBaselineTimeInput(e.target.value)}
              />
            </label>
            <label>
              Error rate (%)
              <input
                type="number"
                min={0}
                max={100}
                value={baselineErrorPercentInput}
                onChange={(e) => setBaselineErrorPercentInput(e.target.value)}
              />
            </label>
            <label>
              Notes
              <textarea value={baselineNotes} onChange={(e) => setBaselineNotes(e.target.value)} rows={2} />
            </label>
            <div className="registry-entry-actions">
              <button type="submit" disabled={savingBaseline}>
                {savingBaseline ? 'Saving...' : 'Save baseline'}
              </button>
              {summary.baseline && (
                <button type="button" className="button-secondary" onClick={handleClearBaseline}>
                  Clear baseline
                </button>
              )}
            </div>
            {baselineError && <p className="error">{baselineError}</p>}
          </form>

          {summary.baseline_comparison && (
            <div data-testid="twin-baseline-comparison">
              <h4>Twin vs. baseline</h4>
              {summary.baseline_comparison.time_delta_seconds !== null && (
                <p className="meta">
                  Average run: {summary.baseline_comparison.average_run_duration_seconds?.toFixed(1)}s (
                  {summary.baseline_comparison.time_delta_seconds > 0 ? '+' : ''}
                  {summary.baseline_comparison.time_delta_seconds.toFixed(1)}s vs. baseline)
                </p>
              )}
              {summary.baseline_comparison.error_rate_delta !== null && (
                <p className="meta">
                  Error rate delta: {summary.baseline_comparison.error_rate_delta > 0 ? '+' : ''}
                  {Math.round(summary.baseline_comparison.error_rate_delta * 100)}pp vs. baseline
                </p>
              )}
            </div>
          )}
        </div>
      )}

      <h3>Scenarios</h3>
      {loading ? (
        <p>Loading...</p>
      ) : scenarios.length === 0 ? (
        <p className="meta">No scenarios defined yet for this agent.</p>
      ) : (
        <ul className="registry-entry-list">
          {scenarios.map((scenario) => {
            const lastRun = runsByScenario[scenario.id]
            return (
              <li key={scenario.id} className="registry-entry-card" data-testid="twin-scenario-card">
                <div className="registry-entry-header">
                  <span>{scenario.name}</span>
                  {lastRun && <span className={statusBadgeClass(lastRun.status)}>{lastRun.status}</span>}
                </div>
                <div className="registry-entry-actions">
                  <button type="button" onClick={() => handleRun(scenario.id)} disabled={runningId === scenario.id}>
                    {runningId === scenario.id ? 'Running...' : 'Run'}
                  </button>
                  <button type="button" className="button-secondary" onClick={() => handleDelete(scenario.id)}>
                    Delete
                  </button>
                </div>
                {lastRun && (
                  <div data-testid="twin-run-result">
                    {lastRun.deviations.length > 0 && (
                      <ul>
                        {lastRun.deviations.map((deviation, i) => (
                          <li key={i} className="error">
                            {deviation.reason}
                          </li>
                        ))}
                      </ul>
                    )}
                    <p className="meta">
                      {lastRun.trace.length} step(s) -- {lastRun.turns_used} turn(s) --{' '}
                      {lastRun.total_cost_usd !== null ? `$${lastRun.total_cost_usd.toFixed(4)}` : 'cost unknown'}
                    </p>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {runError && <p className="error">{runError}</p>}

      <h3>New scenario</h3>
      <form className="twin-scenario-form" onSubmit={handleCreateScenario}>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Inputs (JSON)
          <textarea value={inputsJson} onChange={(e) => setInputsJson(e.target.value)} rows={2} />
        </label>
        <label>
          System stubs (JSON, keyed by system name -- see the artifact's "You have access to" list)
          <textarea value={systemStubsJson} onChange={(e) => setSystemStubsJson(e.target.value)} rows={3} />
        </label>
        <label>
          Human checkpoint config (JSON)
          <textarea value={checkpointJson} onChange={(e) => setCheckpointJson(e.target.value)} rows={2} />
        </label>
        <label>
          Expected steps (JSON list)
          <textarea value={expectedStepsJson} onChange={(e) => setExpectedStepsJson(e.target.value)} rows={3} />
        </label>
        <label>
          Expected outputs (JSON)
          <textarea value={expectedOutputsJson} onChange={(e) => setExpectedOutputsJson(e.target.value)} rows={2} />
        </label>
        <button type="submit" disabled={creating}>
          {creating ? 'Creating...' : 'Add scenario'}
        </button>
        {createError && <p className="error">{createError}</p>}
      </form>
    </div>
  )
}
