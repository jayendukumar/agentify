import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  createDataset,
  getDatasetDiff,
  listAgentArtifacts,
  listDatasetRows,
  listDatasets,
  removeDatasetRow,
  setRowAmbiguity,
  splitDatasetHoldout,
  updateDatasetRow,
} from '../api/client'
import type { AgentArtifact, DatasetDiff, DatasetRow, DatasetSummary, RowDecision } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import DatasetImportWizard from '../components/datasets/DatasetImportWizard'
import DecisionRulesPanel from '../components/datasets/DecisionRulesPanel'
import OraclePanel from '../components/datasets/OraclePanel'
import BatchPanel from '../components/datasets/BatchPanel'

const DECISIONS: (RowDecision | '')[] = ['', 'approve', 'reject', 'escalate']
const PROVENANCE_LABELS: Record<string, string> = {
  historical: 'Real past case',
  synthetic: 'Generated',
  expert: 'Expert-labelled',
  oracle: 'From the rules',
}

type Act = (action: () => Promise<unknown>, success?: string) => Promise<void>

function RowCard({
  processId,
  datasetId,
  row,
  canEdit,
  act,
  onError,
}: {
  processId: string
  datasetId: string
  row: DatasetRow
  canEdit: boolean
  act: Act
  onError: (message: string) => void
}) {
  const [note, setNote] = useState(row.ambiguity_note ?? '')
  const labels = row.labels

  function setDecision(value: string) {
    if (labels === null) return
    void act(() =>
      updateDatasetRow(processId, datasetId, row.row_key, {
        labels: { outputs: labels.outputs, decision: (value || null) as RowDecision | null },
      }),
    )
  }

  function toggleAmbiguous() {
    if (!row.ambiguous && !note.trim()) {
      onError('Say why the case is ambiguous first.')
      return
    }
    void act(() => setRowAmbiguity(processId, datasetId, row.row_key, { ambiguous: !row.ambiguous, note: note || null }))
  }

  return (
    <li className={`registry-entry-card${row.ambiguous ? ' dataset-row-ambiguous' : ''}`}>
      <div className="registry-entry-header">
        <span className="badge">{row.segment}</span>
        <span className="meta">{PROVENANCE_LABELS[row.provenance] ?? row.provenance}</span>
        {row.critical && <span className="badge confidence-low">Critical</span>}
        {row.holdout && <span className="badge">Held out</span>}
        {row.confirmed_at && <span className="badge status-done">Confirmed</span>}
        {row.ambiguous && <span className="badge confidence-medium">Ambiguous</span>}
      </div>
      <dl className="dataset-row-fields">
        {Object.entries(row.inputs).map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{String(value)}</dd>
          </div>
        ))}
      </dl>
      {labels === null ? (
        <p className="meta">Expected result hidden -- this case is held out for unbiased evaluation.</p>
      ) : (
        <div className="dataset-row-labels">
          <label>
            Expected decision
            <select
              disabled={!canEdit}
              value={labels.decision ?? ''}
              onChange={(e) => setDecision(e.target.value)}
              aria-label={`Expected decision for ${row.row_key}`}
            >
              {DECISIONS.map((d) => <option key={d} value={d}>{d || 'not labelled'}</option>)}
            </select>
          </label>
          {Object.keys(labels.outputs).length > 0 && (
            <p className="meta">
              Expected outputs: {Object.entries(labels.outputs).map(([k, v]) => `${k} = ${String(v)}`).join(', ')}
            </p>
          )}
        </div>
      )}
      {row.ambiguous && row.ambiguity_note && <p className="meta">Why ambiguous: {row.ambiguity_note}</p>}
      {row.second_labels && <p className="meta">Second reviewer: {row.second_labels.decision ?? 'no decision'}</p>}
      {canEdit && (
        <div className="registry-entry-actions">
          {!row.confirmed_at && labels !== null && (
            <button
              type="button"
              className="button-secondary"
              onClick={() => act(() => updateDatasetRow(processId, datasetId, row.row_key, { confirm: true }))}
            >
              Confirm label
            </button>
          )}
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why is this unclear?"
            aria-label={`Ambiguity note for ${row.row_key}`}
          />
          <button type="button" className="button-secondary" onClick={toggleAmbiguous}>
            {row.ambiguous ? 'Not ambiguous' : 'Mark ambiguous'}
          </button>
          <button
            type="button"
            className="button-secondary"
            onClick={() => act(() => removeDatasetRow(processId, datasetId, row.row_key))}
          >
            Remove
          </button>
        </div>
      )}
    </li>
  )
}

/** Epic 32: labelled datasets -- the golden cases agents are graded on. */
export default function DatasetsPage() {
  const { processId } = useParams<{ processId: string }>()
  const { can } = useAuth()
  const canEdit = can('agent.test')
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [artifacts, setArtifacts] = useState<AgentArtifact[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [rows, setRows] = useState<DatasetRow[]>([])
  const [total, setTotal] = useState(0)
  const [only, setOnly] = useState('')
  const [diff, setDiff] = useState<DatasetDiff | null>(null)
  const [diffFrom, setDiffFrom] = useState('')
  const [name, setName] = useState('')
  const [artifactId, setArtifactId] = useState('')
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)

  const dataset = datasets.find((d) => d.id === selected) ?? null

  async function load(selectFirst = false) {
    if (!processId) return
    try {
      const [list, agentList] = await Promise.all([listDatasets(processId), listAgentArtifacts(processId)])
      setDatasets(list)
      setArtifacts(agentList)
      if (selectFirst) setSelected(list[0]?.id ?? null)
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not load datasets' })
    }
  }

  async function loadRows() {
    if (!processId || !selected) return
    try {
      const page = await listDatasetRows(processId, selected, { only: only || undefined, limit: 100 })
      setRows(page.rows)
      setTotal(page.total)
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not load cases' })
    }
  }

  useEffect(() => {
    void load(true)
  }, [processId])

  useEffect(() => {
    setDiff(null)
    void loadRows()
  }, [selected, only])

  const act: Act = async (action, success) => {
    try {
      await action()
      if (success) setMessage({ kind: 'info', text: success })
      await Promise.all([load(), loadRows()])
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'That did not work' })
    }
  }

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault()
    if (!processId || !name.trim()) return
    await act(async () => {
      const created = await createDataset(processId, { name: name.trim(), agent_artifact_id: artifactId || null })
      setSelected(created.id)
      setName('')
    }, 'Dataset created.')
  }

  async function showDiff() {
    if (!processId || !selected || !diffFrom) return
    try {
      setDiff(await getDatasetDiff(processId, selected, Number(diffFrom)))
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not compare versions' })
    }
  }

  if (!processId) return <p>Missing process id.</p>

  return (
    <div className="page">
      <p>
        <Link to={`/processes/${processId}`}>&larr; Process</Link>
      </p>
      <h1>Golden cases</h1>
      <p className="meta">
        Cases with the answer each one should get, kept separate from how a test is set up. Run a data-driven test
        against them to see how often an agent gets real cases right.
      </p>
      {message && <p className={message.kind} role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}

      <div className="chip-row">
        {datasets.map((d) => (
          <button
            key={d.id}
            type="button"
            className={`button-secondary chip-button${d.id === selected ? ' tab-button-active' : ''}`}
            onClick={() => setSelected(d.id)}
          >
            {d.name} ({d.row_count})
          </button>
        ))}
      </div>

      <DecisionRulesPanel processId={processId} canEdit={canEdit} />

      {canEdit && (
        <form className="create-form" onSubmit={handleCreate}>
          <label>New dataset<input value={name} onChange={(e) => setName(e.target.value)} placeholder="Refund decisions 2025" /></label>
          <label>
            For agent
            <select value={artifactId} onChange={(e) => setArtifactId(e.target.value)}>
              <option value="">Any</option>
              {artifacts.map((a) => <option key={a.id} value={a.id}>{a.definition.name}</option>)}
            </select>
          </label>
          <button type="submit" className="button-secondary">Create</button>
        </form>
      )}

      {dataset && (
        <section aria-labelledby="dataset-heading">
          <h2 id="dataset-heading">
            {dataset.name} <span className="meta">v{dataset.version}</span>
          </h2>
          <div className="stat-row">
            <span className="stat-card"><span className="stat-value">{dataset.row_count}</span><span className="stat-label">cases</span></span>
            <span className="stat-card"><span className="stat-value">{dataset.confirmed_count}</span><span className="stat-label">confirmed</span></span>
            <span className="stat-card"><span className="stat-value">{dataset.ambiguous_count}</span><span className="stat-label">ambiguous</span></span>
            <span className="stat-card"><span className="stat-value">{dataset.holdout_count}</span><span className="stat-label">held out</span></span>
            {dataset.agreement_rate !== null && (
              <span className="stat-card">
                <span className="stat-value">{Math.round(dataset.agreement_rate * 100)}%</span>
                <span className="stat-label">reviewers agree</span>
              </span>
            )}
          </div>
          <p className="meta">
            Segments: {Object.entries(dataset.segments).map(([k, v]) => `${k} ${v}`).join(', ') || 'none'} · Sources:{' '}
            {Object.entries(dataset.provenance).map(([k, v]) => `${PROVENANCE_LABELS[k] ?? k} ${v}`).join(', ') || 'none'}
          </p>

          {canEdit && (
            <DatasetImportWizard processId={processId} datasetId={dataset.id} onImported={(text) => act(async () => undefined, text)} />
          )}

          {canEdit && dataset.row_count > 0 && (
            <p>
              <button
                type="button"
                className="button-secondary"
                onClick={() => act(() => splitDatasetHoldout(processId, dataset.id, 0.2), 'One case in five is now held out, balanced across segments.')}
              >
                Hold out 20% for unbiased evaluation
              </button>
            </p>
          )}

          <div className="create-form">
            <label>
              Compare with version
              <input type="number" min={1} max={dataset.version} value={diffFrom} onChange={(e) => setDiffFrom(e.target.value)} />
            </label>
            <button type="button" className="button-secondary" onClick={showDiff} disabled={!diffFrom}>Show changes</button>
          </div>
          {diff && (
            <p className="info" role="status">
              v{diff.from_version} to v{diff.to_version}: {diff.added} added, {diff.removed} removed, {diff.relabelled} relabelled,{' '}
              {diff.edited} edited.
            </p>
          )}

          <BatchPanel processId={processId} datasetId={dataset.id} artifactId={dataset.agent_artifact_id} canEdit={canEdit} />

          <OraclePanel processId={processId} datasetId={dataset.id} canEdit={canEdit} onChanged={() => void Promise.all([load(), loadRows()])} />

          <label className="org-kind-picker">
            Show
            <select value={only} onChange={(e) => setOnly(e.target.value)}>
              <option value="">All cases</option>
              <option value="unconfirmed">Not yet confirmed</option>
              <option value="ambiguous">Ambiguous</option>
              <option value="critical">Critical</option>
              <option value="holdout">Held out</option>
            </select>
          </label>
          <p className="meta">{total} cases</p>
          <ul className="registry-entry-list">
            {rows.map((row) => (
              <RowCard
                key={row.revision_id}
                processId={processId}
                datasetId={dataset.id}
                row={row}
                canEdit={canEdit}
                act={act}
                onError={(text) => setMessage({ kind: 'error', text })}
              />
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
