import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  controlProcessTwin,
  estimateProcessTwin,
  getOperatingModel,
  getProcessTwinReport,
  listDatasets,
  listOrchestrationScenarios,
  listProcessTwinBatches,
  saveOperatingModel,
  startProcessTwin,
} from '../api/client'
import type {
  DatasetSummary,
  EfficiencyReport,
  Interval,
  NodeOperatingModel,
  OperatingModelView,
  OrchestrationScenario,
  ProcessTwinBatch,
  ProcessTwinEstimate,
  Rate,
  Triangular,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'
import '../styles/process-twin.css'

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? '--' : `${Math.round(value * 100)}%`
}

function num(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? '--' : value.toLocaleString(undefined, { maximumFractionDigits: digits })
}

function hours(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '--'
  return minutes >= 120 ? `${num(minutes / 60, 1)} h` : `${num(minutes, 1)} min`
}

function range(i: Interval, fmt: (v: number | null) => string): string {
  return `${fmt(i.low)}-${fmt(i.high)}`
}

function RateTile({ label, rate, note }: { label: string; rate: Rate; note: string }) {
  return (
    <div className="stat-card ptwin-tile" title={`95% confidence: ${pct(rate.low)} to ${pct(rate.high)}`}>
      <span className="stat-label">{label}</span>
      <span className="ptwin-tile-value">{pct(rate.rate)}</span>
      <span className="meta">{rate.passed} of {rate.total} · {pct(rate.low)}-{pct(rate.high)} · {note}</span>
    </div>
  )
}

function ValueTile({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="stat-card ptwin-tile">
      <span className="stat-label">{label}</span>
      <span className="ptwin-tile-value">{value}</span>
      <span className="meta">{note}</span>
    </div>
  )
}

/** As-is vs to-be for one measure: two thin bars on one shared scale,
 * each direct-labelled, so identity never rests on colour alone. */
function CompareBars({ title, asIs, toBe, format }: { title: string; asIs: number | null; toBe: number | null; format: (v: number | null) => string }) {
  const max = Math.max(asIs ?? 0, toBe ?? 0, 1e-9)
  const rows = [
    { key: 'as-is', label: 'Today', value: asIs },
    { key: 'to-be', label: 'With agents', value: toBe },
  ]
  return (
    <figure className="ptwin-compare">
      <figcaption>{title}</figcaption>
      {rows.map((row) => (
        <div className="ptwin-compare-row" key={row.key} title={`${row.label}: ${format(row.value)}`}>
          <span className="ptwin-compare-label">{row.label}</span>
          <span className="ptwin-compare-track">
            <span className={`ptwin-bar ptwin-bar-${row.key}`} style={{ width: `${Math.max(1, ((row.value ?? 0) / max) * 100)}%` }} />
          </span>
          <span className="ptwin-compare-value">{format(row.value)}</span>
        </div>
      ))}
    </figure>
  )
}

function TriangularInput({ value, onChange, disabled, label }: { value: Triangular | null; onChange: (v: Triangular | null) => void; disabled: boolean; label: string }) {
  const parts: (keyof Triangular)[] = ['min', 'likely', 'max']
  return (
    <span className="ptwin-tri">
      {parts.map((part) => (
        <input
          key={part}
          type="number"
          min={0}
          step="any"
          aria-label={`${label} ${part}`}
          placeholder={part}
          disabled={disabled}
          value={value ? value[part] : ''}
          onChange={(e) => {
            const raw = e.target.value
            if (raw === '' && !value) return
            const next = { ...(value ?? { min: 0, likely: 0, max: 0 }), [part]: raw === '' ? 0 : Number(raw) }
            onChange(next.min === 0 && next.likely === 0 && next.max === 0 ? null : next)
          }}
        />
      ))}
    </span>
  )
}

function OperatingModelEditor({ processId, canEdit }: { processId: string; canEdit: boolean }) {
  const [model, setModel] = useState<OperatingModelView | null>(null)
  const [message, setMessage] = useState<{ kind: 'error' | 'ok'; text: string } | null>(null)

  useEffect(() => {
    getOperatingModel(processId).then(setModel).catch((err) =>
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not load the operating model' }))
  }, [processId])

  if (!model) return message ? <p className="error" role="alert">{message.text}</p> : <p className="loading-state">Loading...</p>

  function setNode(index: number, patch: Partial<NodeOperatingModel>) {
    setModel((m) => (m ? { ...m, nodes: m.nodes.map((n, i) => (i === index ? { ...n, ...patch } : n)) } : m))
  }

  async function save() {
    if (!model) return
    try {
      setModel(await saveOperatingModel(processId, model))
      setMessage({ kind: 'ok', text: 'Saved.' })
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not save' })
    }
  }

  return (
    <section aria-labelledby="om-heading" className="ptwin-section">
      <h2 id="om-heading">How the process runs today</h2>
      <p className="meta">
        {model.is_default ? 'Not saved yet: pre-filled from the diagram.' : `Version ${model.version}.`} Times are minutes
        (minimum, most likely, maximum). The twin compares every case against these numbers.
      </p>
      <div className="ptwin-model-settings">
        <label>Cases per month <input type="number" min={0} disabled={!canEdit} value={model.monthly_volume} onChange={(e) => setModel({ ...model, monthly_volume: Number(e.target.value) })} /></label>
        <label>Productive hours per FTE a year <input type="number" min={1} disabled={!canEdit} value={model.productive_hours_per_fte} onChange={(e) => setModel({ ...model, productive_hours_per_fte: Number(e.target.value) })} /></label>
        <label>Currency <input disabled={!canEdit} value={model.currency} onChange={(e) => setModel({ ...model, currency: e.target.value })} /></label>
      </div>
      <h3>People and their cost</h3>
      <table className="data-table">
        <thead><tr><th scope="col">Role</th><th scope="col">Cost per hour ({model.currency})</th></tr></thead>
        <tbody>
          {model.roles.map((role, i) => (
            <tr key={role.role}>
              <td>{role.role}</td>
              <td><input type="number" min={0} disabled={!canEdit} aria-label={`${role.role} hourly cost`} value={role.hourly_cost}
                onChange={(e) => setModel({ ...model, roles: model.roles.map((r, j) => (j === i ? { ...r, hourly_cost: Number(e.target.value) } : r)) })} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Steps</h3>
      <div className="ptwin-table-wrap">
        <table className="data-table ptwin-model-table">
          <thead>
            <tr>
              <th scope="col">Step</th><th scope="col">Done today by</th><th scope="col">Hands-on time</th>
              <th scope="col">Waits before it</th><th scope="col">Review when an agent asks</th>
            </tr>
          </thead>
          <tbody>
            {model.nodes.map((node, i) => (
              <tr key={node.node_id}>
                <td>{node.label}</td>
                <td>
                  <select disabled={!canEdit} aria-label={`${node.label} role`} value={node.role} onChange={(e) => setNode(i, { role: e.target.value })}>
                    <option value="">--</option>
                    {model.roles.map((r) => <option key={r.role} value={r.role}>{r.role}</option>)}
                  </select>
                </td>
                <td><TriangularInput label={`${node.label} hands-on`} disabled={!canEdit} value={node.handling_minutes} onChange={(v) => setNode(i, { handling_minutes: v })} /></td>
                <td><TriangularInput label={`${node.label} wait`} disabled={!canEdit} value={node.wait_minutes} onChange={(v) => setNode(i, { wait_minutes: v })} /></td>
                <td><TriangularInput label={`${node.label} review`} disabled={!canEdit} value={node.review_minutes} onChange={(v) => setNode(i, { review_minutes: v })} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>What each ending means</h3>
      <ul className="ptwin-endings">
        {Object.entries(model.end_event_outcomes).map(([id, outcome]) => (
          <li key={id}>
            <code>{id}</code>{' '}
            <input disabled={!canEdit} aria-label={`${id} outcome`} value={outcome}
              onChange={(e) => setModel({ ...model, end_event_outcomes: { ...model.end_event_outcomes, [id]: e.target.value } })} />
          </li>
        ))}
      </ul>
      {canEdit && <button type="button" onClick={save}>Save operating model</button>}
      {message && <p className={message.kind === 'error' ? 'error' : 'status-note'} role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}
    </section>
  )
}

function ReportView({ report, labels }: { report: EfficiencyReport; labels: Record<string, string> }) {
  const cur = report.currency
  const money = (v: number | null) => (v === null ? '--' : `${cur} ${num(v, 2)}`)
  const [showCases, setShowCases] = useState(false)
  const misses = report.cases_detail.filter((c) => c.correct === false)
  return (
    <div className="ptwin-report">
      <section className="results-headline" aria-labelledby="ptwin-headline">
        <h2 id="ptwin-headline">{pct(report.touch_time_reduction.mean)} less people time per case</h2>
        <p>{report.headline}</p>
      </section>

      <div className="stat-row">
        <RateTile label="Straight through" rate={report.straight_through} note="no person involved" />
        <RateTile label="Right outcome" rate={report.accuracy} note="against the labels" />
        <ValueTile label="Cycle time" value={pct(report.cycle_time_reduction.mean)} note={`shorter (${range(report.cycle_time_reduction, pct)})`} />
        <ValueTile label="FTE capacity released" value={num(report.annual_fte_released.mean, 1)} note={`a year (${range(report.annual_fte_released, (v) => num(v, 1))})`} />
        <ValueTile label="Cost saved" value={`${cur} ${num(report.annual_cost_saved.mean)}`} note={`a year (${range(report.annual_cost_saved, (v) => num(v))})`} />
      </div>

      <div className="ptwin-compare-grid">
        <div className="ptwin-legend" aria-hidden="true">
          <span><span className="ptwin-swatch ptwin-bar-as-is" /> Today</span>
          <span><span className="ptwin-swatch ptwin-bar-to-be" /> With agents</span>
        </div>
        <CompareBars title="Cycle time per case (working time)" asIs={report.as_is_cycle_minutes.mean} toBe={report.to_be_cycle_minutes.mean} format={hours} />
        <CompareBars title="People's hands-on time per case" asIs={report.as_is_touch_minutes.mean} toBe={report.to_be_touch_minutes.mean} format={hours} />
        <CompareBars title="Cost per case (labour + AI)" asIs={report.as_is_cost_per_case.mean} toBe={report.to_be_cost_per_case.mean} format={money} />
      </div>

      <section aria-labelledby="ptwin-steps">
        <h2 id="ptwin-steps">Step by step</h2>
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Step</th><th scope="col">Done by</th><th scope="col">Cases</th><th scope="col">Failed</th>
              <th scope="col">Agent time</th><th scope="col">Asked for review</th><th scope="col">AI cost / case</th>
              <th scope="col">People time today</th><th scope="col">People time with agents</th>
            </tr>
          </thead>
          <tbody>
            {report.nodes.map((n) => (
              <tr key={n.node_id} className={n.failures > 0 ? 'results-weak' : undefined}>
                <td>{n.label}</td>
                <td>{n.kind === 'agent' ? 'Agent' : 'Person'}</td>
                <td>{n.visits}</td>
                <td>{n.failures}</td>
                <td>{n.mean_agent_seconds === null ? '--' : `${num(n.mean_agent_seconds, 1)} s`}</td>
                <td>{n.review_rate === null ? '--' : pct(n.review_rate)}</td>
                <td>{n.mean_llm_cost_usd === null ? '--' : `$${num(n.mean_llm_cost_usd, 4)}`}</td>
                <td>{num(n.as_is_touch_minutes, 1)} min</td>
                <td>{num(n.to_be_touch_minutes, 1)} min</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {report.segments.length > 1 && (
        <section aria-labelledby="ptwin-segments">
          <h2 id="ptwin-segments">By kind of case</h2>
          <table className="data-table">
            <thead>
              <tr><th scope="col">Kind of case</th><th scope="col">Cases</th><th scope="col">Right outcome</th><th scope="col">Straight through</th><th scope="col">People time saved / case</th></tr>
            </thead>
            <tbody>
              {report.segments.map((seg) => (
                <tr key={seg.segment} className={seg.accuracy.rate !== null && seg.accuracy.rate < 0.8 ? 'results-weak' : undefined}>
                  <td>{seg.segment.replaceAll('_', ' ')}</td>
                  <td>{seg.cases}</td>
                  <td title={`95% confidence: ${pct(seg.accuracy.low)} to ${pct(seg.accuracy.high)}`}>{pct(seg.accuracy.rate)} <span className="meta">({seg.accuracy.passed}/{seg.accuracy.total})</span></td>
                  <td>{pct(seg.straight_through)}</td>
                  <td>{num(seg.mean_touch_saved_minutes, 1)} min</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section aria-labelledby="ptwin-outcomes">
        <h2 id="ptwin-outcomes">Outcomes: expected vs what the twin did</h2>
        <table className="data-table">
          <thead><tr><th scope="col">Expected</th><th scope="col">Twin</th><th scope="col">Cases</th></tr></thead>
          <tbody>
            {report.outcome_matrix.map((cell) => (
              <tr key={`${cell.expected}-${cell.actual}`} className={cell.expected === cell.actual ? 'results-diagonal' : undefined}>
                <td>{cell.expected}</td><td className={cell.expected === cell.actual ? undefined : 'results-miss'}>{cell.actual}</td><td>{cell.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {misses.length > 0 && (
          <p className="meta">Wrong outcomes: {misses.map((c) => c.row_key).join(', ')}</p>
        )}
      </section>

      <section aria-labelledby="ptwin-paths">
        <h2 id="ptwin-paths">Routes cases took</h2>
        <ol className="ptwin-paths">
          {report.path_variants.map((v) => (
            <li key={v.path.join('>')}><strong>{v.cases}</strong> {v.path.map((id) => labels[id] ?? id).join(' → ')}</li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="ptwin-cases">
        <h2 id="ptwin-cases">Cases</h2>
        <button type="button" className="button-secondary" aria-expanded={showCases} onClick={() => setShowCases(!showCases)}>
          {showCases ? 'Hide' : 'Show'} all {report.cases} cases
        </button>
        {showCases && (
          <div className="ptwin-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Case</th><th scope="col">Kind</th><th scope="col">Outcome</th><th scope="col">Expected</th><th scope="col">People touches</th>
                  <th scope="col">Cycle today</th><th scope="col">Cycle with agents</th><th scope="col">AI cost</th><th scope="col">Note</th>
                </tr>
              </thead>
              <tbody>
                {report.cases_detail.map((c) => (
                  <tr key={c.run_id} className={c.correct === false ? 'results-weak' : undefined}>
                    <td>{c.row_key}</td><td>{c.segment.replaceAll('_', ' ')}</td><td>{c.outcome ?? c.status.toLowerCase()}</td><td>{c.expected_outcome ?? '--'}</td>
                    <td>{c.human_touches}</td><td>{hours(c.as_is_cycle_minutes)}</td><td>{hours(c.to_be_cycle_minutes)}</td>
                    <td>${num(c.llm_cost_usd, 4)}</td><td className="meta">{c.failure ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <details>
        <summary>How these numbers are worked out</summary>
        <ul>{report.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      </details>
    </div>
  )
}

/** Epic 36: run the whole agentic process as a digital twin over many
 * cases and compare it with how the process runs today. */
export default function ProcessTwinPage() {
  const { processId } = useParams<{ processId: string }>()
  const { can } = useAuth()
  const canEdit = can('agent.test')
  const [scenarios, setScenarios] = useState<OrchestrationScenario[]>([])
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [batches, setBatches] = useState<ProcessTwinBatch[]>([])
  const [scenarioId, setScenarioId] = useState('')
  const [datasetId, setDatasetId] = useState('')
  const [sampleSize, setSampleSize] = useState('')
  const [budget, setBudget] = useState('2')
  const [estimate, setEstimate] = useState<ProcessTwinEstimate | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [report, setReport] = useState<EfficiencyReport | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function loadBatches() {
    if (!processId) return
    const list = await listProcessTwinBatches(processId)
    setBatches(list)
    setSelected((s) => s ?? list[0]?.id ?? null)
  }

  useEffect(() => {
    if (!processId) return
    Promise.all([listOrchestrationScenarios(processId), listDatasets(processId)])
      .then(([sc, ds]) => {
        setScenarios(sc)
        setDatasets(ds)
        setScenarioId(sc[0]?.id ?? '')
        setDatasetId(ds[0]?.id ?? '')
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load scenarios'))
    void loadBatches()
  }, [processId])

  const current = batches.find((b) => b.id === selected) ?? null
  useEffect(() => {
    if (!processId || !selected) return
    getProcessTwinReport(processId, selected).then(setReport).catch(() => setReport(null))
  }, [processId, selected, current?.finished])

  useEffect(() => {
    if (!batches.some((b) => b.status === 'running' || b.status === 'paused')) return
    const interval = setInterval(() => void loadBatches(), 5000)
    return () => clearInterval(interval)
  }, [batches])

  const request = useMemo(() => ({
    scenario_id: scenarioId,
    dataset_id: datasetId,
    sample_size: sampleSize ? Number(sampleSize) : null,
    budget_usd: budget ? Number(budget) : null,
  }), [scenarioId, datasetId, sampleSize, budget])

  async function runEstimate() {
    if (!processId) return
    try { setEstimate(await estimateProcessTwin(processId, request)) } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not estimate')
    }
  }

  async function start() {
    if (!processId) return
    try {
      const batch = await startProcessTwin(processId, request)
      setSelected(batch.id)
      await loadBatches()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start')
    }
  }

  async function control(action: 'pause' | 'resume' | 'cancel') {
    if (!processId || !current) return
    await controlProcessTwin(processId, current.id, action)
    await loadBatches()
  }

  if (!processId) return <p>Missing process.</p>
  const labels = Object.fromEntries((report?.nodes ?? []).map((n) => [n.node_id, n.label]))

  return (
    <div className="page process-twin">
      <p><Link to={`/processes/${processId}`}>&larr; Process</Link> · <Link to={`/processes/${processId}/blueprint`}>Blueprint &amp; agents</Link></p>
      <h1>Process twin</h1>
      <p className="lede">
        Run the whole process, every agent together, over realistic cases before anything reaches production, and
        compare it with how the process runs today.
      </p>
      {error && <p className="error" role="alert">{error}</p>}

      <OperatingModelEditor processId={processId} canEdit={canEdit} />

      <section aria-labelledby="run-heading" className="ptwin-section">
        <h2 id="run-heading">Run the twin</h2>
        <div className="ptwin-run-form">
          <label>Rehearsal
            <select value={scenarioId} onChange={(e) => setScenarioId(e.target.value)}>
              {scenarios.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label>Cases
            <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
              {datasets.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.row_count})</option>)}
            </select>
          </label>
          <label>Sample (blank = all) <input type="number" min={1} value={sampleSize} onChange={(e) => setSampleSize(e.target.value)} /></label>
          <label>Budget (USD) <input type="number" min={0} step="0.5" value={budget} onChange={(e) => setBudget(e.target.value)} /></label>
        </div>
        <p>
          <button type="button" className="button-secondary" disabled={!scenarioId || !datasetId} onClick={runEstimate}>Estimate</button>{' '}
          {canEdit && <button type="button" disabled={!scenarioId || !datasetId} onClick={start}>Run the process twin</button>}
        </p>
        {estimate && (
          <p className="meta">{estimate.cases} cases, about ${num(estimate.total_cost_usd, 2)} (${num(estimate.cost_per_case_usd, 4)} a case; {estimate.basis}).</p>
        )}
        {batches.length > 0 && (
          <table className="data-table">
            <thead><tr><th scope="col">Run</th><th scope="col">Status</th><th scope="col">Progress</th><th scope="col">Spent</th><th scope="col" /></tr></thead>
            <tbody>
              {batches.map((b) => (
                <tr key={b.id} aria-selected={b.id === selected}>
                  <td>{b.name}</td>
                  <td><span className="badge">{b.status.replace('_', ' ')}</span></td>
                  <td><progress max={b.cases} value={b.finished} aria-label={`${b.finished} of ${b.cases} cases`} /> {b.finished}/{b.cases}</td>
                  <td>${num(b.spent_usd, 3)}</td>
                  <td><button type="button" className="button-secondary" onClick={() => setSelected(b.id)}>{b.id === selected ? 'Showing' : 'Show'}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {current && canEdit && (current.status === 'running' || current.status === 'paused') && (
          <p>
            {current.status === 'running'
              ? <button type="button" className="button-secondary" onClick={() => control('pause')}>Pause</button>
              : <button type="button" className="button-secondary" onClick={() => control('resume')}>Resume</button>}{' '}
            <button type="button" className="button-secondary" onClick={() => control('cancel')}>Cancel</button>
          </p>
        )}
      </section>

      {report && current && (
        <section aria-labelledby="report-heading" className="ptwin-section">
          <h2 id="report-heading" className="visually-hidden">Results</h2>
          {current.status === 'running' && <p className="status-note" role="status">Results so far: {current.finished} of {current.cases} cases finished.</p>}
          <ReportView report={report} labels={labels} />
        </section>
      )}
    </div>
  )
}
