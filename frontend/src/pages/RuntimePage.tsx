import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  ApiError,
  driftCasesToDataset,
  getDrift,
  getRolloutComparison,
  getRuntimeExecution,
  listDatasets,
  listDeploymentExecutions,
  listRuntimeDeployments,
  promoteDeployment,
  rollbackDeployment,
  updateMonitoring,
  type MetricsWindow,
} from '../api/client'
import type {
  AlertRule,
  DatasetSummary,
  DriftCheck,
  DriftFinding,
  Rate,
  RolloutComparison,
  RunStep,
  RuntimeDeployment,
  RuntimeExecution,
  RuntimeExecutionSummary,
  RuntimeMetrics,
  RuntimeOverviewRow,
  VersionNumbers,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'
import StepInspector from '../components/StepInspector'

// Epic 39, step 4: every deployed agent, how it's doing over the last
// hour, day or week (US39.14), how a new version compares with the one it
// would replace and stepping it up or back (US39.13), and whether it still
// behaves the way it did in the twin (US39.15).

const WINDOWS: { id: MetricsWindow; label: string }[] = [
  { id: 'hour', label: 'Last hour' },
  { id: 'day', label: 'Last day' },
  { id: 'week', label: 'Last week' },
]
const SHARES = [5, 25, 100]
const ALERT_METRICS = [
  'error_rate', 'escalation_rate', 'reject_rate', 'latency_p95_seconds', 'latency_p50_seconds', 'cost_usd',
  'cost_per_case_usd', 'tool_errors', 'policy_denials', 'escalations', 'executions', 'tokens', 'reviews_open',
  'review_wait_minutes_max',
]

function message(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function when(value: string): string {
  return new Date(value.endsWith('Z') || value.includes('+') ? value : `${value}Z`).toLocaleString()
}

function pct(rate: Rate | null | undefined): string {
  if (!rate || rate.rate === null) return '–'
  return `${Math.round(rate.rate * 100)}%`
}

function interval(rate: Rate): string {
  if (rate.rate === null || rate.low === null || rate.high === null) return 'no cases'
  return `${pct(rate)} (${Math.round(rate.low * 100)}–${Math.round(rate.high * 100)}%, ${rate.passed} of ${rate.total})`
}

function seconds(value: number | null): string {
  if (value === null) return '–'
  return value < 60 ? `${value.toFixed(1)}s` : `${(value / 60).toFixed(1)} min`
}

function money(value: number | null): string {
  return value === null ? '–' : `$${value.toFixed(value < 1 ? 4 : 2)}`
}

function stageLabel(d: RuntimeDeployment): string {
  if (d.status === 'shadow') return 'Shadow'
  if (d.status === 'active' && d.traffic_share) return `Canary ${d.traffic_share}%`
  return d.status.charAt(0).toUpperCase() + d.status.slice(1)
}

function stageClass(d: RuntimeDeployment): string {
  if (d.status === 'killed') return 'badge status-failed'
  if (d.status === 'paused') return 'badge status-warning'
  if (d.status === 'active') return 'badge status-done'
  return 'badge'
}

function DriftBadge({ row }: { row: RuntimeOverviewRow }) {
  const drift = row.drift
  if (!row.deployment.has_baseline) return <span className="meta">No baseline</span>
  if (!drift) return <span className="meta">Not checked</span>
  if (drift.status === 'drift') return <span className="badge status-failed">Drifted</span>
  if (drift.status === 'input_drift') return <span className="badge status-warning">Cases changed</span>
  if (drift.status === 'not_enough') return <span className="meta">Too few cases</span>
  return <span className="badge status-done">As in the twin</span>
}

function Overview({ rows, selected, onSelect }: {
  rows: RuntimeOverviewRow[]
  selected: string | null
  onSelect: (id: string) => void
}) {
  return (
    <table className="org-register runtime-table">
      <thead>
        <tr>
          <th>Agent</th>
          <th>Process</th>
          <th>Status</th>
          <th>Cases</th>
          <th>Failed</th>
          <th>Escalated</th>
          <th>p95 time</th>
          <th>Cost</th>
          <th>Drift</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const d = row.deployment
          const m = row.metrics
          return (
            <tr key={d.id} aria-selected={selected === d.id} className={selected === d.id ? 'selected' : undefined}>
              <td>
                <button type="button" className="link-button" onClick={() => onSelect(d.id)}>
                  {d.agent_name} v{d.artifact_version}
                </button>
                {row.alerts.length > 0 && <span className="badge status-warning" title={row.alerts[0].message}>Alert</span>}
              </td>
              <td>{d.process_name}</td>
              <td><span className={stageClass(d)}>{stageLabel(d)}</span></td>
              <td>{m.executions}</td>
              <td>{pct(m.error_rate)}</td>
              <td>{pct(m.escalation_rate)}</td>
              <td>{seconds(m.latency_seconds.p95)}</td>
              <td>{money(m.cost_usd)}</td>
              <td><DriftBadge row={row} /></td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function MetricsPanel({ m }: { m: RuntimeMetrics }) {
  const tools = Object.entries(m.tools)
  return (
    <section aria-label="Metrics">
      <h3>How it's doing</h3>
      <dl className="runtime-metrics">
        <div><dt>Cases</dt><dd>{m.executions} ({Object.entries(m.by_status).map(([s, n]) => `${n} ${s.toLowerCase()}`).join(', ') || 'none'})</dd></div>
        <div><dt>Failed</dt><dd>{interval(m.error_rate)}</dd></div>
        <div><dt>Escalated</dt><dd>{interval(m.escalation_rate)}</dd></div>
        <div><dt>Time per case</dt><dd>p50 {seconds(m.latency_seconds.p50)} · p95 {seconds(m.latency_seconds.p95)}</dd></div>
        <div><dt>Cost</dt><dd>{money(m.cost_usd)} ({money(m.cost_per_case_usd)} a case) · {m.tokens.toLocaleString()} tokens</dd></div>
        <div><dt>Denied by policy</dt><dd>{m.policy_denials}</dd></div>
        <div>
          <dt>Reviews</dt>
          <dd>
            {m.reviews.approved} approved, {m.reviews.rejected} rejected ({pct(m.reviews.reject_rate)} rejected) · {m.reviews.open} open
            {m.reviews.wait_minutes_median !== null && ` · median wait ${m.reviews.wait_minutes_median} min`}
            {m.reviews.open > 0 && ` · longest waiting ${m.reviews.waiting_now_minutes_max} min`}
          </dd>
        </div>
        {m.shadow_copies > 0 && <div><dt>Shadow copies</dt><dd>{m.shadow_copies}</dd></div>}
      </dl>
      {tools.length > 0 && (
        <table className="org-register">
          <thead><tr><th>Tool</th><th>Calls</th><th>Errors</th><th>Not sent (shadow)</th></tr></thead>
          <tbody>
            {tools.map(([name, t]) => (
              <tr key={name}><td>{name}</td><td>{t.calls}</td><td>{t.errors}</td><td>{t.not_sent}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      {m.stops.length > 0 && (
        <ul className="meta">
          {m.stops.map((s) => (
            <li key={`${s.action}-${s.at}`}>{when(s.at)}: {s.action.replace(/_/g, ' ')}{s.reason ? ` — ${s.reason}` : ''}</li>
          ))}
        </ul>
      )}
    </section>
  )
}

function ComparisonTable({ comparison }: { comparison: RolloutComparison }) {
  const fresh = comparison.numbers.new
  const old = comparison.numbers.old
  const rows: [string, (n: VersionNumbers) => string][] = [
    ['Cases', (n) => String(n.cases)],
    ['Decisions', (n) => JSON.stringify(n.decision_mix)],
    ['Escalated', (n) => interval(n.escalation_rate)],
    ['Failed', (n) => interval(n.error_rate)],
    ['Cost a case', (n) => money(n.cost_per_case_usd)],
  ]
  const known = comparison.paired && comparison.paired.with_known_outcome > 0 ? comparison.paired : null
  return (
    <table className="org-register">
      <thead>
        <tr><th /><th>v{comparison.version} (new)</th>{old && <th>v{comparison.against?.version} (now)</th>}</tr>
      </thead>
      <tbody>
        {rows.map(([label, show]) => (
          <tr key={label}><td>{label}</td><td>{show(fresh)}</td>{old && <td>{show(old)}</td>}</tr>
        ))}
        {known && (
          <tr><td>Right, where known</td><td>{interval(known.accuracy_new)}</td><td>{interval(known.accuracy_old)}</td></tr>
        )}
      </tbody>
    </table>
  )
}

function RolloutActions({ deployment, onChanged }: { deployment: RuntimeDeployment; onChanged: () => void }) {
  const { can } = useAuth()
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const steps = SHARES.filter((s) => s > (deployment.traffic_share ?? 0))

  async function act(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
      onChanged()
    } catch (err) {
      setError(message(err, 'That did not work'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {can('agent.approve') && (
        <div className="inbox-actions">
          {steps.map((share) => (
            <button key={share} type="button" disabled={busy}
              onClick={() => act(() => promoteDeployment(deployment.process_id, deployment.id, share))}>
              {share === 100 ? 'Take all cases' : `Take ${share}% of cases`}
            </button>
          ))}
        </div>
      )}
      {can('runtime.stop') && (
        <div className="inbox-actions">
          <label>
            Why roll back
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <button type="button" className="button-secondary" disabled={busy || !reason.trim()}
            onClick={() => act(() => rollbackDeployment(deployment.process_id, deployment.id, reason))}>
            Roll back
          </button>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </>
  )
}

function inRollout(d: RuntimeDeployment): boolean {
  return d.status === 'shadow' || (d.status === 'active' && Boolean(d.traffic_share))
}

function Rollout({ deployment, onChanged }: { deployment: RuntimeDeployment; onChanged: () => void }) {
  const [comparison, setComparison] = useState<RolloutComparison | null>(null)
  const [error, setError] = useState<string | null>(null)
  const active = inRollout(deployment)

  useEffect(() => {
    if (!active) return
    getRolloutComparison(deployment.process_id, deployment.id)
      .then(setComparison)
      .catch((err) => setError(message(err, 'Could not compare the versions')))
  }, [deployment.process_id, deployment.id, active])

  if (!active) return null
  return (
    <section aria-label="Rollout">
      <h3>Rollout: {stageLabel(deployment)}</h3>
      {comparison && (
        <>
          <p>{comparison.reading}</p>
          <ComparisonTable comparison={comparison} />
          <p className="meta">{comparison.method}</p>
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <RolloutActions deployment={deployment} onChanged={onChanged} />
    </section>
  )
}

function Findings({ title, findings }: { title: string; findings: DriftFinding[] | undefined }) {
  if (!findings || findings.length === 0) return null
  return (
    <>
      <h4>{title}</h4>
      <ul className="drift-findings">
        {findings.map((f) => (
          <li key={f.metric} className={f.flagged ? 'flagged' : undefined}>
            {f.flagged && <span className="badge status-failed">Flagged</span>} {f.reading}{' '}
            <span className="meta">({f.statistic})</span>
          </li>
        ))}
      </ul>
    </>
  )
}

function Baseline({ check }: { check: DriftCheck }) {
  const { baseline } = check
  if (!baseline.available || !baseline.pass_rate) return null
  return (
    <p className="meta">
      Baseline: {interval(baseline.pass_rate)} correct in the twin over {baseline.runs} runs. Live window: the last{' '}
      {check.window_days} days, from {check.min_executions} cases. A flag{' '}
      {check.action === 'pause' ? 'pauses the agent' : 'notifies the owners'}.
    </p>
  )
}

function AddCasesToDataset({ deployment }: { deployment: RuntimeDeployment }) {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [datasetId, setDatasetId] = useState('')
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    listDatasets(deployment.process_id).then(setDatasets).catch(() => setDatasets([]))
  }, [deployment.process_id])

  async function addCases() {
    try {
      const { added } = await driftCasesToDataset(deployment.process_id, deployment.id, datasetId)
      setNote(`${added} case(s) added to the dataset.`)
    } catch (err) {
      setNote(message(err, 'Could not add the cases'))
    }
  }

  if (datasets.length === 0) return null
  return (
    <div className="inbox-actions">
      <label>
        Add these cases to
        <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
          <option value="">(choose a dataset)</option>
          {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </label>
      <button type="button" disabled={!datasetId} onClick={addCases}>Add cases</button>
      {note && <p role="status">{note}</p>}
    </div>
  )
}

function Drift({ deployment }: { deployment: RuntimeDeployment }) {
  const { can } = useAuth()
  const [check, setCheck] = useState<DriftCheck | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    getDrift(deployment.process_id, deployment.id)
      .then((result) => {
        setCheck(result)
        setError(null)
      })
      .catch((err) => setError(message(err, 'Could not check for drift')))
  }, [deployment.process_id, deployment.id])

  useEffect(load, [load])
  const hasCases = (check?.cases ?? 0) > 0
  return (
    <section aria-label="Drift">
      <h3>Against the twin</h3>
      {check && (
        <>
          <p>{check.reading}</p>
          <Baseline check={check} />
          <Findings title="The agent" findings={check.behaviour} />
          <Findings title="The cases" findings={check.inputs} />
        </>
      )}
      <button type="button" className="button-secondary" onClick={load}>Check now</button>
      {hasCases && can('agent.test') && <AddCasesToDataset deployment={deployment} />}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  )
}

function stepLabel(s: RunStep): string {
  return s.type.replace(/_/g, ' ').toLowerCase() + (s.target ? ` · ${s.target}` : '')
}

function ExecutionView({ execution }: { execution: RuntimeExecution }) {
  const [step, setStep] = useState<RunStep | null>(null)
  return (
    <div className="runtime-execution">
      <h4>Case {execution.id}</h4>
      {execution.final_output && <pre>{JSON.stringify(execution.final_output, null, 2)}</pre>}
      {execution.reported_outcome && <p className="meta">Reported outcome: {JSON.stringify(execution.reported_outcome)}</p>}
      <div className="runtime-steps">
        <ol>
          {execution.steps.map((s) => (
            <li key={`${s.seq}-${s.occurred_at}`}>
              <button type="button" className="link-button" onClick={() => setStep(s)}>{stepLabel(s)}</button>
            </li>
          ))}
        </ol>
        <StepInspector step={step} allSteps={execution.steps} />
      </div>
    </div>
  )
}

function ExecutionList({ items, onOpen }: { items: RuntimeExecutionSummary[]; onOpen: (id: string) => void }) {
  if (items.length === 0) return <p className="empty-state">No cases yet.</p>
  return (
    <ul className="runtime-executions">
      {items.slice(0, 50).map((e) => (
        <li key={e.id}>
          <button type="button" className="link-button" onClick={() => onOpen(e.id)}>{when(e.created_at)}</button>{' '}
          <span className="badge">{e.status.toLowerCase()}</span>
          {e.shadow_of && <span className="badge">shadow copy</span>}
          <span className="meta"> {e.step_count} steps · {money(e.total_cost_usd)}</span>
        </li>
      ))}
    </ul>
  )
}

function Executions({ deployment }: { deployment: RuntimeDeployment }) {
  const [items, setItems] = useState<RuntimeExecutionSummary[] | null>(null)
  const [open, setOpen] = useState<RuntimeExecution | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listDeploymentExecutions(deployment.process_id, deployment.id)
      .then(setItems)
      .catch((err) => setError(message(err, 'Could not load the cases')))
  }, [deployment.process_id, deployment.id])

  async function show(id: string) {
    try {
      setOpen(await getRuntimeExecution(deployment.process_id, id))
    } catch (err) {
      setError(message(err, 'Could not load the case'))
    }
  }

  return (
    <section aria-label="Cases">
      <h3>Cases</h3>
      {error && <p className="error" role="alert">{error}</p>}
      {items !== null && <ExecutionList items={items} onOpen={show} />}
      {open && <ExecutionView key={open.id} execution={open} />}
    </section>
  )
}

function RuleList({ rules, onRemove }: { rules: AlertRule[]; onRemove: (id: string | undefined) => void }) {
  if (rules.length === 0) return <p className="meta">No alert rules.</p>
  return (
    <ul>
      {rules.map((r) => (
        <li key={r.id}>
          {r.name || r.metric} over the last {r.window} {r.op} {r.threshold}
          {r.webhook ? ` → ${r.webhook}` : ''} {r.action === 'pause' && <span className="badge">pauses</span>}{' '}
          <button type="button" className="link-button" onClick={() => onRemove(r.id)}>Remove</button>
        </li>
      ))}
    </ul>
  )
}

function RuleForm({ onAdd }: { onAdd: (rule: AlertRule) => void }) {
  const [draft, setDraft] = useState<AlertRule>({ metric: 'error_rate', window: 'hour', op: '>', threshold: 0.1, action: 'notify' })
  return (
    <div className="inbox-actions">
      <label>
        Metric
        <select value={draft.metric} onChange={(e) => setDraft({ ...draft, metric: e.target.value })}>
          {ALERT_METRICS.map((m) => <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>)}
        </select>
      </label>
      <label>
        Window
        <select value={draft.window} onChange={(e) => setDraft({ ...draft, window: e.target.value as AlertRule['window'] })}>
          {WINDOWS.map((w) => <option key={w.id} value={w.id}>{w.id}</option>)}
        </select>
      </label>
      <label>
        Is
        <select value={draft.op} onChange={(e) => setDraft({ ...draft, op: e.target.value as AlertRule['op'] })}>
          {['>', '>=', '<', '<='].map((op) => <option key={op} value={op}>{op}</option>)}
        </select>
      </label>
      <label>
        Threshold
        <input type="number" step="any" value={draft.threshold}
          onChange={(e) => setDraft({ ...draft, threshold: Number(e.target.value) })} />
      </label>
      <label>
        Webhook (optional)
        <input value={draft.webhook ?? ''} onChange={(e) => setDraft({ ...draft, webhook: e.target.value || null })} />
      </label>
      <label>
        <input type="checkbox" checked={draft.action === 'pause'}
          onChange={(e) => setDraft({ ...draft, action: e.target.checked ? 'pause' : 'notify' })} /> Also pause the agent
      </label>
      <button type="button" onClick={() => onAdd(draft)}>Add rule</button>
    </div>
  )
}

function Monitoring({ deployment, onChanged }: { deployment: RuntimeDeployment; onChanged: () => void }) {
  const [rules, setRules] = useState<AlertRule[]>(deployment.alert_rules)
  const [endpoint, setEndpoint] = useState(deployment.telemetry.otlp_endpoint ?? '')
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null)

  async function save(nextRules: AlertRule[]) {
    setStatus(null)
    try {
      const updated = await updateMonitoring(deployment.process_id, deployment.id, {
        alert_rules: nextRules,
        telemetry: { ...deployment.telemetry, otlp_endpoint: endpoint.trim() || undefined },
      })
      setRules(updated.alert_rules)
      setStatus({ ok: true, text: 'Saved.' })
      onChanged()
    } catch (err) {
      setStatus({ ok: false, text: message(err, 'Could not save') })
    }
  }

  return (
    <section aria-label="Alerts and telemetry">
      <h3>Alerts and telemetry</h3>
      <RuleList rules={rules} onRemove={(id) => save(rules.filter((x) => x.id !== id))} />
      <RuleForm onAdd={(rule) => save([...rules, rule])} />
      <div className="inbox-actions">
        <label>
          OpenTelemetry (OTLP/HTTP) endpoint
          <input value={endpoint} placeholder="https://otel.example.com:4318" onChange={(e) => setEndpoint(e.target.value)} />
        </label>
        <button type="button" className="button-secondary" onClick={() => save(rules)}>Save endpoint</button>
      </div>
      {status && <p className={status.ok ? undefined : 'error'} role={status.ok ? 'status' : 'alert'}>{status.text}</p>}
    </section>
  )
}

function Detail({ row, onChanged }: { row: RuntimeOverviewRow; onChanged: () => void }) {
  const { can } = useAuth()
  const d = row.deployment
  return (
    <section className="runtime-detail" aria-label={`${d.agent_name} v${d.artifact_version}`}>
      <h2>
        {d.agent_name} v{d.artifact_version} <span className={stageClass(d)}>{stageLabel(d)}</span>
      </h2>
      {d.pause_reason && d.status === 'paused' && <p className="meta">Paused: {d.pause_reason}</p>}
      {d.stop_reason && <p className="meta">Stopped: {d.stop_reason}</p>}
      {row.alerts.length > 0 && (
        <ul className="inbox-notes">
          {row.alerts.slice(0, 5).map((a) => <li key={`${a.at}-${a.message}`}><span className="meta">{when(a.at)}</span> {a.message}</li>)}
        </ul>
      )}
      <MetricsPanel m={row.metrics} />
      <Rollout deployment={d} onChanged={onChanged} />
      <Drift deployment={d} />
      <Executions deployment={d} />
      {can('org.admin') && <Monitoring deployment={d} onChanged={onChanged} />}
    </section>
  )
}

function WindowPicker({ period, onPick }: { period: MetricsWindow; onPick: (w: MetricsWindow) => void }) {
  return (
    <div role="group" aria-label="Window" className="segmented">
      {WINDOWS.map((w) => (
        <button key={w.id} type="button" aria-pressed={period === w.id}
          className={period === w.id ? undefined : 'button-secondary'} onClick={() => onPick(w.id)}>
          {w.label}
        </button>
      ))}
    </div>
  )
}

function Listing({ rows, selected, onSelect }: {
  rows: RuntimeOverviewRow[] | null
  selected: string | null
  onSelect: (id: string) => void
}) {
  if (rows === null) return <p className="loading-state" role="status">Loading...</p>
  if (rows.length === 0) return <p className="empty-state">No agent is deployed yet.</p>
  return <Overview rows={rows} selected={selected} onSelect={onSelect} />
}

export default function RuntimePage() {
  const [params, setParams] = useSearchParams()
  const [period, setPeriod] = useState<MetricsWindow>('day')
  const [rows, setRows] = useState<RuntimeOverviewRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const selected = params.get('deployment')

  const load = useCallback(() => {
    listRuntimeDeployments(period)
      .then((r) => {
        setRows(r)
        setError(null)
      })
      .catch((err) => setError(message(err, 'Failed to load the deployments')))
  }, [period])

  useEffect(load, [load])

  const row = rows?.find((r) => r.deployment.id === selected) ?? null
  return (
    <div className="page">
      <h1>Runtime</h1>
      <p className="meta">
        Every agent deployed to production, with how it's doing. A new version can run in shadow (it sees live cases and
        sends nothing that changes data) or as a canary taking a share of cases, before it takes them all.
      </p>
      <WindowPicker period={period} onPick={setPeriod} />
      {error ? <p className="error" role="alert">{error}</p>
        : <Listing rows={rows} selected={selected} onSelect={(id) => setParams({ deployment: id })} />}
      {row && <Detail key={row.deployment.id} row={row} onChanged={load} />}
    </div>
  )
}
