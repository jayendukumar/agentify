import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  cancelOrchestrationRun,
  createOrchestrationScenario,
  deleteOrchestrationScenario,
  getOrchestrationRun,
  listOrchestrationRuns,
  listOrchestrationScenarios,
  resumeOrchestrationRun,
  startOrchestrationRun,
} from '../api/client'
import type {
  DataMappingMode,
  GatewayDecision,
  ManualNodeConfig,
  NodeRun,
  OrchestrationRun,
  OrchestrationScenario,
  TwinHumanCheckpointConfig,
  TwinSystemStub,
} from '../api/types'
import { isTerminalRunStatus, runStatusBadgeClass } from '../lib/runStatus'

// US16.7: poll rather than push (websockets/SSE) -- same live-observability
// mechanism app/ingestion/pipeline.py already uses for document processing
// status (ProcessDetailPage.tsx polls every 4s); this run can involve
// several agents each making several LLM calls, so 2s keeps the timeline
// feeling live without hammering the API.
const POLL_INTERVAL_MS = 2000

function parseJsonField<T>(raw: string, fieldLabel: string): T {
  try {
    return JSON.parse(raw) as T
  } catch {
    throw new Error(`${fieldLabel} is not valid JSON`)
  }
}

const NODE_RUN_KIND_LABELS: Record<NodeRun['kind'], string> = {
  agent: 'agent',
  manual: 'manual step',
  gateway: 'gateway',
  start_event: 'start',
  end_event: 'end',
}

export default function OrchestrationPanel({ processId }: { processId: string }) {
  const [scenarios, setScenarios] = useState<OrchestrationScenario[]>([])
  const [runsByScenario, setRunsByScenario] = useState<Record<string, OrchestrationRun>>({})
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [startingId, setStartingId] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [inputsJson, setInputsJson] = useState('{}')
  const [gatewayDecisionsJson, setGatewayDecisionsJson] = useState('{}')
  const [manualNodeConfigJson, setManualNodeConfigJson] = useState('{}')
  const [systemStubsJson, setSystemStubsJson] = useState('{}')
  const [checkpointConfigJson, setCheckpointConfigJson] = useState('{}')
  const [dataMappingModeJson, setDataMappingModeJson] = useState('{}')
  const [expectedPathJson, setExpectedPathJson] = useState('[]')
  const [expectedFinalOutputJson, setExpectedFinalOutputJson] = useState('{}')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  function startPolling(scenarioId: string, runId: string) {
    const existing = pollTimers.current[scenarioId]
    if (existing) clearInterval(existing)
    const timer = setInterval(async () => {
      try {
        const run = await getOrchestrationRun(processId, runId)
        setRunsByScenario((prev) => ({ ...prev, [scenarioId]: run }))
        // WAITING_FOR_HUMAN is a stable resting state too -- nothing
        // changes until a person resumes it, so stop polling there as
        // well, not just at a true terminal status.
        if (isTerminalRunStatus(run.status) || run.status === 'WAITING_FOR_HUMAN') {
          clearInterval(timer)
          delete pollTimers.current[scenarioId]
        }
      } catch (err) {
        clearInterval(timer)
        delete pollTimers.current[scenarioId]
        setRunError(err instanceof ApiError ? err.message : 'Lost connection while watching the run')
      }
    }, POLL_INTERVAL_MS)
    pollTimers.current[scenarioId] = timer
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)

    Promise.all([listOrchestrationScenarios(processId), listOrchestrationRuns(processId)])
      .then(([scenarioList, runList]) => {
        if (cancelled) return
        setScenarios(scenarioList)
        // listOrchestrationRuns returns most-recent-first -- keep only the
        // first (latest) run seen per scenario.
        const latestByScenario: Record<string, OrchestrationRun> = {}
        for (const run of runList) {
          if (!latestByScenario[run.scenario_id]) latestByScenario[run.scenario_id] = run
        }
        setRunsByScenario(latestByScenario)
        for (const run of Object.values(latestByScenario)) {
          if (!isTerminalRunStatus(run.status) && run.status !== 'WAITING_FOR_HUMAN') {
            startPolling(run.scenario_id, run.id)
          }
        }
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processId])

  useEffect(() => {
    const timers = pollTimers.current
    return () => {
      for (const timer of Object.values(timers)) clearInterval(timer)
    }
  }, [])

  async function handleCreateScenario(event: React.FormEvent) {
    event.preventDefault()
    setCreateError(null)
    try {
      const body = {
        name,
        inputs: parseJsonField<Record<string, unknown>>(inputsJson, 'Start-event inputs'),
        gateway_decisions: parseJsonField<Record<string, GatewayDecision>>(gatewayDecisionsJson, 'Gateway decisions'),
        manual_node_config: parseJsonField<Record<string, ManualNodeConfig>>(
          manualNodeConfigJson,
          'Manual node config',
        ),
        system_stubs: parseJsonField<Record<string, Record<string, TwinSystemStub>>>(
          systemStubsJson,
          'System stubs',
        ),
        human_checkpoint_config: parseJsonField<Record<string, TwinHumanCheckpointConfig>>(
          checkpointConfigJson,
          'Human checkpoint config',
        ),
        data_mapping_mode: parseJsonField<Record<string, DataMappingMode>>(dataMappingModeJson, 'Data mapping mode'),
        expected_path: parseJsonField<string[]>(expectedPathJson, 'Expected path'),
        expected_final_output: parseJsonField<Record<string, unknown>>(
          expectedFinalOutputJson,
          'Expected final output',
        ),
      }
      setCreating(true)
      const scenario = await createOrchestrationScenario(processId, body)
      setScenarios((prev) => [...prev, scenario])
      setName('')
    } catch (err) {
      setCreateError(
        err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Failed to create scenario',
      )
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(scenarioId: string) {
    if (!window.confirm('Delete this scenario? This cannot be undone.')) return
    setRunError(null)
    try {
      await deleteOrchestrationScenario(processId, scenarioId)
      setScenarios((prev) => prev.filter((s) => s.id !== scenarioId))
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : 'Could not delete the scenario. Please try again.')
    }
  }

  async function handleStartRun(scenarioId: string) {
    setStartingId(scenarioId)
    setRunError(null)
    try {
      const run = await startOrchestrationRun(processId, scenarioId)
      setRunsByScenario((prev) => ({ ...prev, [scenarioId]: run }))
      startPolling(scenarioId, run.id)
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : 'Failed to start the rehearsal run')
    } finally {
      setStartingId(null)
    }
  }

  // Epic 17, US17.3/US17.4: a WAITING_FOR_HUMAN run needs a real person's
  // decision (or a cancellation) to move again -- resuming re-enters the
  // same background execution, so polling picks back up exactly as it did
  // for the initial run.
  async function handleResumeRun(runId: string, scenarioId: string, decision: 'approve' | 'reject') {
    setRunError(null)
    try {
      const run = await resumeOrchestrationRun(processId, runId, decision)
      setRunsByScenario((prev) => ({ ...prev, [scenarioId]: run }))
      startPolling(scenarioId, runId)
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : 'Failed to resume the run')
    }
  }

  async function handleCancelRun(runId: string, scenarioId: string) {
    setRunError(null)
    try {
      await cancelOrchestrationRun(processId, runId)
      // A WAITING_FOR_HUMAN run has no active background task, so
      // app/db/repository.py's cancel_run resolves it to CANCELLED
      // immediately -- refetch once rather than poll for a change that
      // already happened.
      const run = await getOrchestrationRun(processId, runId)
      setRunsByScenario((prev) => ({ ...prev, [scenarioId]: run }))
    } catch (err) {
      setRunError(err instanceof ApiError ? err.message : 'Failed to cancel the run')
    }
  }

  if (loading) {
    return (
      <div className="page" data-testid="orchestration-panel">
        <p>Loading...</p>
      </div>
    )
  }

  return (
    <div className="page" data-testid="orchestration-panel">
      {loadError && (
        <p className="error" role="alert">
          {loadError}
        </p>
      )}
      <p className="meta">
        Runs every automatable node's generated agent together along the real process flow, handing data off
        between them and simulating manual steps and gateways along the way -- see the Digital Twin tabs to test
        one agent in isolation instead.
      </p>

      <h3>Scenarios</h3>
      {scenarios.length === 0 ? (
        <p className="meta">No rehearsal scenarios defined yet.</p>
      ) : (
        <ul className="registry-entry-list">
          {scenarios.map((scenario) => {
            const run = runsByScenario[scenario.id]
            return (
              <li key={scenario.id} className="registry-entry-card" data-testid="orchestration-scenario-card">
                <div className="registry-entry-header">
                  <span>{scenario.name}</span>
                  {run && <span className={runStatusBadgeClass(run.status, run.graded_passed)}>{run.status}</span>}
                </div>
                <div className="registry-entry-actions">
                  <button
                    type="button"
                    onClick={() => handleStartRun(scenario.id)}
                    disabled={startingId === scenario.id || (!!run && !isTerminalRunStatus(run.status))}
                  >
                    {run && !isTerminalRunStatus(run.status)
                      ? 'Running...'
                      : startingId === scenario.id
                        ? 'Starting...'
                        : 'Run'}
                  </button>
                  <button type="button" className="button-secondary" onClick={() => handleDelete(scenario.id)}>
                    Delete
                  </button>
                  {run?.status === 'WAITING_FOR_HUMAN' && (
                    <>
                      <button type="button" onClick={() => handleResumeRun(run.id, scenario.id, 'approve')}>
                        Approve
                      </button>
                      <button
                        type="button"
                        className="button-secondary"
                        onClick={() => handleResumeRun(run.id, scenario.id, 'reject')}
                      >
                        Reject
                      </button>
                      <button
                        type="button"
                        className="button-secondary"
                        onClick={() => handleCancelRun(run.id, scenario.id)}
                      >
                        Cancel
                      </button>
                    </>
                  )}
                </div>
                {run && (
                  <div data-testid="orchestration-run-result">
                    {run.deviations.length > 0 && (
                      <ul>
                        {run.deviations.map((deviation, i) => (
                          <li key={i} className="error">
                            {deviation.reason}
                          </li>
                        ))}
                      </ul>
                    )}
                    <p className="meta">
                      {run.node_runs.length} node(s) visited --{' '}
                      {run.total_cost_usd !== null ? `$${run.total_cost_usd.toFixed(4)}` : 'cost unknown'}
                    </p>
                    <ol data-testid="orchestration-timeline">
                      {run.node_runs.map((nodeRun, i) => (
                        <li key={`${nodeRun.node_id}-${i}`} data-testid="orchestration-node-run">
                          <span className={runStatusBadgeClass(nodeRun.status)}>{nodeRun.status}</span>{' '}
                          <strong>{nodeRun.node_label}</strong>{' '}
                          <span className="meta">({NODE_RUN_KIND_LABELS[nodeRun.kind]})</span>
                          {nodeRun.error_message && <p className="error">{nodeRun.error_message}</p>}
                          {nodeRun.steps.length > 0 && (
                            <p className="meta">{nodeRun.steps.length} step(s)</p>
                          )}
                        </li>
                      ))}
                      {!isTerminalRunStatus(run.status) && run.status !== 'WAITING_FOR_HUMAN' && (
                        <li className="meta" data-testid="orchestration-run-live">
                          Running -- watching live...
                        </li>
                      )}
                      {run.status === 'WAITING_FOR_HUMAN' && (
                        <li className="meta" data-testid="orchestration-run-waiting">
                          Waiting for a human decision...
                        </li>
                      )}
                    </ol>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {runError && (
        <p className="error" role="alert">
          {runError}
        </p>
      )}

      <h3>New scenario</h3>
      <form className="twin-scenario-form" onSubmit={handleCreateScenario}>
        <label>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} required />
        </label>
        <label>
          Start-event inputs (JSON)
          <textarea value={inputsJson} onChange={(e) => setInputsJson(e.target.value)} rows={2} />
        </label>
        <label>
          Gateway decisions (JSON, keyed by gateway node id -- e.g. {'{"Gateway_1": {"to_node_id": "Task_2"}}'})
          <textarea value={gatewayDecisionsJson} onChange={(e) => setGatewayDecisionsJson(e.target.value)} rows={2} />
        </label>
        <label>
          Manual node config (JSON, keyed by node id -- default mode is human_checkpoint)
          <textarea
            value={manualNodeConfigJson}
            onChange={(e) => setManualNodeConfigJson(e.target.value)}
            rows={2}
          />
        </label>
        <label>
          System stubs (JSON, keyed by node id, then by system name)
          <textarea value={systemStubsJson} onChange={(e) => setSystemStubsJson(e.target.value)} rows={2} />
        </label>
        <label>
          Human checkpoint config (JSON, keyed by node id -- for an automatable agent's own checkpoint, if it has
          one)
          <textarea
            value={checkpointConfigJson}
            onChange={(e) => setCheckpointConfigJson(e.target.value)}
            rows={2}
          />
        </label>
        <label>
          Data mapping mode (JSON, keyed by the receiving node id -- "llm_adapter" (default) or
          "exact_field_contract")
          <textarea
            value={dataMappingModeJson}
            onChange={(e) => setDataMappingModeJson(e.target.value)}
            rows={2}
          />
        </label>
        <label>
          Expected path (JSON list of node ids, optional)
          <textarea value={expectedPathJson} onChange={(e) => setExpectedPathJson(e.target.value)} rows={2} />
        </label>
        <label>
          Expected final output (JSON)
          <textarea
            value={expectedFinalOutputJson}
            onChange={(e) => setExpectedFinalOutputJson(e.target.value)}
            rows={2}
          />
        </label>
        <button type="submit" disabled={creating}>
          {creating ? 'Creating...' : 'Add scenario'}
        </button>
        {createError && (
          <p className="error" role="alert">
            {createError}
          </p>
        )}
      </form>
    </div>
  )
}
