import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ApiError,
  controlBatch,
  createBatchSchedule,
  deleteBatchSchedule,
  estimateBatch,
  listBatchSchedules,
  listBatches,
  listScenarios,
  startBatch,
} from '../../api/client'
import type { BatchEstimate, BatchRequest, BatchSchedule, BatchTier, BatchView, TwinScenario } from '../../api/types'

const TIERS: { value: BatchTier; label: string }[] = [
  { value: 'smoke', label: 'Smoke -- about 10 cases, after every change' },
  { value: 'regression', label: 'Regression -- a few hundred, the release check' },
  { value: 'full', label: 'Full -- every case' },
]

const STATUS_LABELS: Record<BatchView['status'], string> = {
  queued: 'Queued',
  running: 'Running',
  paused: 'Paused',
  cancelled: 'Cancelled',
  completed: 'Finished',
  stopped_budget: 'Stopped: budget reached',
}

/** Epic 34: run a data-driven scenario over this dataset at volume. */
export default function BatchPanel({
  processId,
  datasetId,
  artifactId,
  canEdit,
}: {
  processId: string
  datasetId: string
  artifactId: string | null
  canEdit: boolean
}) {
  const [scenarios, setScenarios] = useState<TwinScenario[]>([])
  const [scenarioId, setScenarioId] = useState('')
  const [tier, setTier] = useState<BatchTier>('smoke')
  const [trials, setTrials] = useState('1')
  const [budget, setBudget] = useState('')
  const [mode, setMode] = useState<'correctness' | 'load'>('correctness')
  const [concurrency, setConcurrency] = useState('8')
  const [latency, setLatency] = useState('200')
  const [estimate, setEstimate] = useState<BatchEstimate | null>(null)
  const [batchList, setBatchList] = useState<BatchView[]>([])
  const [schedules, setSchedules] = useState<BatchSchedule[]>([])
  const [every, setEvery] = useState('')
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)

  async function load() {
    try {
      const own = artifactId ? (await listScenarios(processId, artifactId)).filter((s) => s.dataset_id === datasetId) : []
      setScenarios(own)
      if (!scenarioId && own[0]) setScenarioId(own[0].id)
      const ids = new Set(own.map((s) => s.id))
      const [allBatches, allSchedules] = await Promise.all([listBatches(processId), listBatchSchedules(processId)])
      setBatchList(allBatches.filter((b) => ids.has(b.scenario_id)))
      setSchedules(allSchedules.filter((s) => ids.has(s.scenario_id)))
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not load batches' })
    }
  }

  useEffect(() => {
    void load()
  }, [processId, datasetId, artifactId])

  useEffect(() => {
    if (!batchList.some((b) => b.status === 'running' || b.status === 'queued')) return
    const interval = setInterval(load, 4000)
    return () => clearInterval(interval)
  }, [batchList])

  function request(): BatchRequest {
    return {
      tier,
      trials: Number(trials) || 1,
      budget_usd: budget ? Number(budget) : null,
      mode,
      target_concurrency: Number(concurrency) || 4,
      stub_latency_ms: mode === 'load' ? Number(latency) || 0 : 0,
    }
  }

  async function handleEstimate() {
    setMessage(null)
    try {
      setEstimate((await estimateBatch(processId, scenarioId, request())).estimate)
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not estimate' })
    }
  }

  async function handleStart() {
    try {
      await startBatch(processId, scenarioId, request())
      setEstimate(null)
      setMessage({ kind: 'info', text: 'Batch started.' })
      await load()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not start the batch' })
    }
  }

  async function act(batchId: string, action: 'pause' | 'resume' | 'cancel') {
    try {
      await controlBatch(processId, batchId, action)
      await load()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'That did not work' })
    }
  }

  async function handleSchedule() {
    try {
      await createBatchSchedule(processId, { scenario_id: scenarioId, tier, trials: Number(trials) || 1, every_minutes: every ? Number(every) : null, budget_usd: budget ? Number(budget) : null })
      setMessage({ kind: 'info', text: 'Scheduled. It also runs when the cases or the rules change.' })
      await load()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not schedule' })
    }
  }

  return (
    <section aria-labelledby="batch-heading">
      <h2 id="batch-heading">Run at volume</h2>
      {message && <p className={message.kind} role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}
      {scenarios.length === 0 ? (
        <p className="empty-state">
          No test uses these cases yet. Create a data-driven test for the agent (a scenario with this dataset) to run it here.
        </p>
      ) : (
        <>
          {canEdit && (
            <div className="org-upload-form">
              <fieldset>
                <legend>New batch</legend>
                <label>
                  Test
                  <select value={scenarioId} onChange={(e) => setScenarioId(e.target.value)}>
                    {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </label>
                <label>
                  How much
                  <select value={tier} onChange={(e) => setTier(e.target.value as BatchTier)}>
                    {TIERS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </label>
                <label>Times each case runs<input type="number" min={1} max={10} value={trials} onChange={(e) => setTrials(e.target.value)} /></label>
                <label>Stop when spend reaches ($)<input type="number" min={0} step="0.01" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="no limit" /></label>
                <label>
                  Purpose
                  <select value={mode} onChange={(e) => setMode(e.target.value as 'correctness' | 'load')}>
                    <option value="correctness">Is it right? (correctness)</option>
                    <option value="load">Does it hold up? (load)</option>
                  </select>
                </label>
                {mode === 'load' && (
                  <>
                    <label>At once<input type="number" min={1} max={64} value={concurrency} onChange={(e) => setConcurrency(e.target.value)} /></label>
                    <label>Simulated system delay (ms)<input type="number" min={0} value={latency} onChange={(e) => setLatency(e.target.value)} /></label>
                  </>
                )}
                <div className="registry-entry-actions">
                  <button type="button" className="button-secondary" onClick={handleEstimate}>Estimate</button>
                  {estimate && <button type="button" className="button-primary" onClick={handleStart}>Start {estimate.runs} runs</button>}
                  <input value={every} onChange={(e) => setEvery(e.target.value)} placeholder="every N minutes (optional)" aria-label="Schedule interval" />
                  <button type="button" className="button-secondary" onClick={handleSchedule}>Schedule</button>
                </div>
                {estimate && (
                  <p className="info">
                    {estimate.runs} runs ({estimate.rows} cases x {estimate.trials}), about ${estimate.total_cost_usd.toFixed(2)} and{' '}
                    {estimate.total_minutes} minutes -- based on {estimate.basis}.
                  </p>
                )}
              </fieldset>
            </div>
          )}

          <ul className="registry-entry-list">
            {batchList.map((b) => {
              const pct = b.progress.total ? Math.round((b.progress.finished / b.progress.total) * 100) : 0
              const active = b.status === 'running' || b.status === 'queued' || b.status === 'paused'
              return (
                <li key={b.id} className="registry-entry-card">
                  <div className="registry-entry-header">
                    <strong>{b.tier} {b.mode === 'load' ? 'load test' : 'batch'}</strong>
                    <span className={`badge ${b.status === 'completed' ? 'status-done' : b.status === 'running' ? 'status-processing' : ''}`}>{STATUS_LABELS[b.status]}</span>
                    <span className="meta">{new Date(b.created_at).toLocaleString()} · agent v{b.agent_version} · cases v{b.dataset_version}</span>
                    {b.estimate.trigger && <span className="meta">({b.estimate.trigger})</span>}
                  </div>
                  <div className="processing-progress-bar batch-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Batch progress">
                    <span style={{ width: `${pct}%` }} />
                  </div>
                  <p className="meta">
                    {b.progress.finished} of {b.progress.total} done · {b.progress.passed} passed · {b.progress.failed} failed
                    {b.progress.errored ? ` · ${b.progress.errored} errored` : ''}
                    {b.progress.cancelled ? ` · ${b.progress.cancelled} not run` : ''} · ${b.spent_usd.toFixed(2)} spent
                    {b.budget_usd ? ` of $${b.budget_usd.toFixed(2)}` : ''}
                  </p>
                  <div className="registry-entry-actions">
                    {canEdit && b.status === 'running' && <button type="button" className="button-secondary" onClick={() => act(b.id, 'pause')}>Pause</button>}
                    {canEdit && b.status === 'paused' && <button type="button" className="button-secondary" onClick={() => act(b.id, 'resume')}>Resume</button>}
                    {canEdit && active && <button type="button" className="button-secondary" onClick={() => act(b.id, 'cancel')}>Cancel</button>}
                    {b.progress.finished > 0 && (
                      <Link className="button-link" to={`/processes/${processId}/batches/${b.id}`}>Results &rarr;</Link>
                    )}
                  </div>
                </li>
              )
            })}
          </ul>

          {schedules.length > 0 && (
            <>
              <h3>Scheduled</h3>
              <ul>
                {schedules.map((s) => (
                  <li key={s.id}>
                    {s.tier}, {s.trials}x{s.every_minutes ? `, every ${s.every_minutes} min` : ''}
                    {s.on_dataset_change ? ', when the cases change' : ''}
                    {s.on_context_change ? ', when the rules change' : ''}
                    {s.last_run_at ? ` -- last ran ${new Date(s.last_run_at).toLocaleString()}` : ''}{' '}
                    {canEdit && (
                      <button type="button" className="link-button" onClick={async () => { await deleteBatchSchedule(processId, s.id); await load() }}>
                        Remove
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  )
}
