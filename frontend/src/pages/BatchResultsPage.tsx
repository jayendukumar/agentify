import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  compareBatches,
  getBatch,
  getBatchEvidence,
  getBatchResults,
  getReleaseCriteria,
  getReleaseGate,
  listBatches,
  setReleaseCriteria,
} from '../api/client'
import type { BatchComparison, BatchResults, BatchView, GateResult, Rate, ReleaseCriteria } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { downloadText } from '../lib/exportPng'

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? '--' : `${Math.round(value * 100)}%`
}

function RateCell({ rate }: { rate: Rate }) {
  return (
    <span title={`95% confidence: ${pct(rate.low)} to ${pct(rate.high)}`}>
      {pct(rate.rate)} <span className="meta">({rate.passed}/{rate.total}, {pct(rate.low)}-{pct(rate.high)})</span>
    </span>
  )
}

const DEFAULT_CRITERIA: ReleaseCriteria = {
  min_pass_rate_lower: 0.9,
  min_segment_rate: 0.8,
  critical_must_pass: true,
  no_significant_regression: true,
  tiers: ['regression', 'full'],
}

/** Epic 35: one batch's results -- the headline with its confidence,
 * where it fails and why, how consistent it is, how it compares with
 * another version, and whether it clears the release gate. */
export default function BatchResultsPage() {
  const { processId, batchId } = useParams<{ processId: string; batchId: string }>()
  const { user } = useAuth()
  const canEdit = user?.role === 'editor'
  const [batch, setBatch] = useState<BatchView | null>(null)
  const [results, setResults] = useState<BatchResults | null>(null)
  const [others, setOthers] = useState<BatchView[]>([])
  const [compareWith, setCompareWith] = useState('')
  const [comparison, setComparison] = useState<BatchComparison | null>(null)
  const [criteria, setCriteria] = useState<ReleaseCriteria>(DEFAULT_CRITERIA)
  const [gate, setGate] = useState<GateResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    if (!processId || !batchId) return
    try {
      const b = await getBatch(processId, batchId)
      const [r, list, saved, g] = await Promise.all([
        getBatchResults(processId, batchId),
        listBatches(processId, b.scenario_id),
        getReleaseCriteria(processId, b.agent_artifact_id),
        getReleaseGate(processId, b.agent_artifact_id),
      ])
      setBatch(b)
      setResults(r)
      setOthers(list.filter((x) => x.id !== batchId && x.mode === 'correctness'))
      if (saved) setCriteria(saved)
      setGate(g)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load results')
    }
  }

  useEffect(() => {
    void load()
  }, [processId, batchId])

  useEffect(() => {
    if (!batch || (batch.status !== 'running' && batch.status !== 'queued')) return
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [batch])

  async function runCompare(otherId: string) {
    setCompareWith(otherId)
    if (!processId || !batchId || !otherId) return
    try {
      setComparison(await compareBatches(processId, otherId, batchId))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not compare')
    }
  }

  async function saveCriteria() {
    if (!processId || !batch) return
    try {
      setCriteria(await setReleaseCriteria(processId, batch.agent_artifact_id, criteria))
      setGate(await getReleaseGate(processId, batch.agent_artifact_id))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the criteria')
    }
  }

  async function exportEvidence() {
    if (!processId || !batchId) return
    const evidence = await getBatchEvidence(processId, batchId)
    downloadText(JSON.stringify(evidence, null, 2), `batch-evidence-${batchId}.json`, 'application/json')
  }

  if (!processId || !batchId) return <p>Missing batch.</p>
  if (!results || !batch) return error ? <p className="error" role="alert">{error}</p> : <p className="loading-state" role="status">Loading results...</p>

  const decisions = [...new Set([...Object.keys(results.decision_matrix), ...Object.values(results.decision_matrix).flatMap((r) => Object.keys(r))])]

  return (
    <div className="page batch-results">
      <p><Link to={`/processes/${processId}/datasets`}>&larr; Golden cases</Link></p>
      <h1>{batch.tier} {batch.mode === 'load' ? 'load test' : 'batch'} results</h1>
      <p className="meta">
        Agent v{batch.agent_version} · cases v{batch.dataset_version} · {batch.rows} cases x {batch.trials} · {batch.status}
        {batch.status === 'running' && ' (results so far)'}
      </p>
      {error && <p className="error" role="alert">{error}</p>}

      <section className="results-headline" aria-labelledby="headline-heading">
        <h2 id="headline-heading">{pct(results.headline.rate)} correct</h2>
        <p>{results.reading}</p>
        {Object.keys(results.excluded).length > 0 && (
          <p className="meta">Not counted: {Object.entries(results.excluded).map(([k, v]) => `${v} ${k.replace('_', ' ')}`).join(', ')}.</p>
        )}
        {results.critical_failures.length > 0 && (
          <p className="error">Critical cases failed: {results.critical_failures.join(', ')}</p>
        )}
        <button type="button" className="button-secondary" onClick={exportEvidence}>Export evidence</button>
      </section>

      {results.load && (
        <section aria-labelledby="load-heading">
          <h2 id="load-heading">Under load</h2>
          <p>
            {results.load.runs} runs · median {results.load.p50_seconds}s · 95th percentile {results.load.p95_seconds}s · 99th percentile{' '}
            {results.load.p99_seconds}s · {pct(results.load.failure_rate)} failed · {results.load.throughput_per_minute ?? '--'} per minute
          </p>
        </section>
      )}

      <section aria-labelledby="segments-heading">
        <h2 id="segments-heading">Where it does well and badly</h2>
        <table className="org-register">
          <thead><tr><th>Segment</th><th>Correct</th></tr></thead>
          <tbody>
            {results.segments.map((s) => (
              <tr key={s.segment} className={s.weak ? 'results-weak' : ''}>
                <td>{s.segment}{s.weak && <span className="badge confidence-low">weak</span>}</td>
                <td><RateCell rate={s.rate} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="meta">By where the answers came from: {results.provenance.map((p) => `${p.segment} ${pct(p.rate.rate)}`).join(' · ')}</p>
      </section>

      {decisions.length > 0 && (
        <section aria-labelledby="matrix-heading">
          <h2 id="matrix-heading">How it decides</h2>
          <table className="org-register">
            <thead>
              <tr><th>Should be (row) / was (column)</th>{decisions.map((d) => <th key={d}>{d}</th>)}</tr>
            </thead>
            <tbody>
              {Object.entries(results.decision_matrix).map(([expected, row]) => (
                <tr key={expected}>
                  <th>{expected}</th>
                  {decisions.map((d) => <td key={d} className={d === expected ? 'results-diagonal' : row[d] ? 'results-miss' : ''}>{row[d] ?? 0}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          {Object.keys(results.field_accuracy).length > 0 && (
            <p className="meta">Output fields: {Object.entries(results.field_accuracy).map(([f, r]) => `${f} ${pct(r.rate)}`).join(' · ')}</p>
          )}
        </section>
      )}

      <section aria-labelledby="clusters-heading">
        <h2 id="clusters-heading">What goes wrong</h2>
        {results.clusters.length === 0 ? (
          <p className="empty-state">No failures.</p>
        ) : (
          <ul className="registry-entry-list">
            {results.clusters.map((c) => (
              <li key={c.pattern} className="registry-entry-card">
                <strong>{c.count} runs</strong>: {c.example_reason}
                <p className="meta">Cases: {c.row_keys.join(', ')}</p>
              </li>
            ))}
          </ul>
        )}
      </section>

      {results.consistency.trials > 1 && (
        <section aria-labelledby="consistency-heading">
          <h2 id="consistency-heading">How consistent</h2>
          <p>
            {results.consistency.always_pass} cases always right, {results.consistency.always_fail} always wrong,{' '}
            <strong>{results.consistency.inconsistent} sometimes right</strong>. Right at least once: {pct(results.consistency.pass_at_k)} ·
            right every time: {pct(results.consistency.pass_hat_k)}.
          </p>
          {results.consistency.inconsistent_rows.length > 0 && <p className="meta">Inconsistent: {results.consistency.inconsistent_rows.join(', ')}</p>}
        </section>
      )}

      {results.relations.length > 0 && (
        <section aria-labelledby="relations-heading">
          <h2 id="relations-heading">Things that must hold</h2>
          <ul>
            {results.relations.map((r) => (
              <li key={r.relation_id}>
                {r.name}: held in {r.held} of {r.checked}{r.broken_rows.length ? ` -- broke for ${r.broken_rows.join(', ')}` : ''}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="cost-heading">
        <h2 id="cost-heading">Cost and time per run</h2>
        <p>
          Cost: median ${results.cost_usd.median?.toFixed(4) ?? '--'}, 95th percentile ${results.cost_usd.p95?.toFixed(4) ?? '--'}, worst $
          {results.cost_usd.max?.toFixed(4) ?? '--'} · Time: median {results.seconds.median ?? '--'}s, 95th percentile {results.seconds.p95 ?? '--'}s ·
          Tokens: median {results.tokens.median ?? '--'}
        </p>
      </section>

      <section aria-labelledby="compare-heading">
        <h2 id="compare-heading">Compare with another batch</h2>
        <select value={compareWith} onChange={(e) => runCompare(e.target.value)} aria-label="Batch to compare with">
          <option value="">Choose...</option>
          {others.map((o) => <option key={o.id} value={o.id}>{new Date(o.created_at).toLocaleString()} -- agent v{o.agent_version}, {o.tier}</option>)}
        </select>
        {comparison && (
          <div className={comparison.significant ? 'info' : 'meta'} role="status">
            <p>{comparison.reading}</p>
            <p className="meta">
              {comparison.paired_rows} cases in both · before {pct(comparison.rate_a.rate)}, now {pct(comparison.rate_b.rate)}
              {comparison.regressed.length > 0 && ` · broke: ${comparison.regressed.join(', ')}`}
            </p>
          </div>
        )}
      </section>

      <section aria-labelledby="gate-heading">
        <h2 id="gate-heading">Release criteria</h2>
        {gate && (
          <p className={gate.passed ? 'info' : 'error'} role="status">
            {gate.passed ? 'This agent version meets its release criteria.' : `Not ready: ${gate.reasons.join('; ')}`}
          </p>
        )}
        {canEdit && (
          <div className="create-form">
            <label>
              Lowest acceptable pass rate (at 95% confidence)
              <input type="number" min={0} max={100} value={Math.round(criteria.min_pass_rate_lower * 100)} onChange={(e) => setCriteria({ ...criteria, min_pass_rate_lower: Number(e.target.value) / 100 })} />
            </label>
            <label>
              Every segment at least (%)
              <input type="number" min={0} max={100} value={criteria.min_segment_rate === null ? '' : Math.round(criteria.min_segment_rate * 100)} onChange={(e) => setCriteria({ ...criteria, min_segment_rate: e.target.value === '' ? null : Number(e.target.value) / 100 })} />
            </label>
            <label className="check-row">
              <input type="checkbox" checked={criteria.critical_must_pass} onChange={(e) => setCriteria({ ...criteria, critical_must_pass: e.target.checked })} />
              Critical cases must all pass
            </label>
            <label className="check-row">
              <input type="checkbox" checked={criteria.no_significant_regression} onChange={(e) => setCriteria({ ...criteria, no_significant_regression: e.target.checked })} />
              No significant regression against the previous version
            </label>
            <button type="button" className="button-secondary" onClick={saveCriteria}>Save criteria</button>
          </div>
        )}
      </section>
    </div>
  )
}
