import { useEffect, useState } from 'react'
import { ApiError, listDatasets, promoteRunToDataset } from '../../api/client'
import type { DatasetSummary, RowDecision } from '../../api/types'

/** Epic 32, US32.7: turn a failed or surprising run into a golden case,
 * with the answer it should have reached. */
export default function PromoteRunControl({ processId, runId }: { processId: string; runId: string }) {
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [datasetId, setDatasetId] = useState('')
  const [decision, setDecision] = useState<RowDecision | ''>('')
  const [critical, setCritical] = useState(false)
  const [status, setStatus] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)

  useEffect(() => {
    listDatasets(processId)
      .then((list) => {
        setDatasets(list)
        setDatasetId(list[0]?.id ?? '')
      })
      .catch(() => setDatasets([]))
  }, [processId])

  if (datasets.length === 0) return null

  async function handlePromote() {
    try {
      await promoteRunToDataset(processId, datasetId, {
        run_id: runId,
        labels: { outputs: {}, decision: decision || null },
        critical,
      })
      setStatus({ kind: 'info', text: 'Added to the golden cases.' })
    } catch (err) {
      setStatus({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not add it' })
    }
  }

  return (
    <div className="create-form promote-run" aria-label="Add this case to golden cases">
      <label>
        Add this case to
        <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}>
          {datasets.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </label>
      <label>
        The right decision was
        <select value={decision} onChange={(e) => setDecision(e.target.value as RowDecision | '')}>
          <option value="">(not a decision case)</option>
          <option value="approve">approve</option>
          <option value="reject">reject</option>
          <option value="escalate">escalate</option>
        </select>
      </label>
      <label className="check-row">
        <input type="checkbox" checked={critical} onChange={(e) => setCritical(e.target.checked)} />
        Must never fail
      </label>
      <button type="button" className="button-secondary" onClick={handlePromote} disabled={!datasetId}>Add to golden cases</button>
      {status && <p className={status.kind} role="status">{status.text}</p>}
    </div>
  )
}
