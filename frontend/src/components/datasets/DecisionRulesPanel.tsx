import { useEffect, useState } from 'react'
import { ApiError, listDecisionRules, proposeDecisionRules, updateDecisionRule } from '../../api/client'
import type { DecisionRule } from '../../api/types'

const STATUS: Record<DecisionRule['status'], { label: string; badge: string }> = {
  confirmed: { label: 'Confirmed', badge: 'status-done' },
  proposed: { label: 'Needs your check', badge: 'confidence-medium' },
  unstructurable: { label: "Can't be automated", badge: 'confidence-low' },
}

function RuleRow({ processId, rule, canEdit, onSaved }: { processId: string; rule: DecisionRule; canEdit: boolean; onSaved: () => void }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(JSON.stringify(rule.expression ?? { field: '', op: 'eq', value: '' }, null, 1))
  const [error, setError] = useState<string | null>(null)

  async function save(body: Parameters<typeof updateDecisionRule>[2]) {
    setError(null)
    try {
      await updateDecisionRule(processId, rule.id, body)
      setEditing(false)
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save')
    }
  }

  function saveExpression() {
    try {
      void save({ expression: JSON.parse(text) })
    } catch {
      setError('That is not valid JSON.')
    }
  }

  return (
    <li className="registry-entry-card">
      <div className="registry-entry-header">
        <strong>{rule.branch_label || rule.branch_target_id}</strong>
        <span className={`badge ${STATUS[rule.status].badge}`}>{STATUS[rule.status].label}</span>
        {rule.is_default && <span className="badge">Otherwise</span>}
      </div>
      <p className="meta">Diagram says: {rule.condition_text || '(no condition)'}</p>
      {rule.rendered && <p><code>{rule.rendered}</code></p>}
      {rule.note && rule.status !== 'confirmed' && <p className="meta">{rule.note}</p>}
      <label>
        Decides the case as
        <select
          disabled={!canEdit}
          value={rule.outcome ?? ''}
          onChange={(e) => save({ outcome: e.target.value })}
          aria-label={`Outcome of ${rule.branch_label}`}
        >
          <option value="">(doesn't decide)</option>
          <option value="approve">approve</option>
          <option value="reject">reject</option>
          <option value="escalate">escalate</option>
        </select>
      </label>
      {canEdit && (
        <div className="registry-entry-actions">
          {rule.status !== 'confirmed' && (rule.expression || rule.is_default) && (
            <button type="button" className="button-primary" onClick={() => save({ confirm: true })}>Confirm</button>
          )}
          <button type="button" className="button-secondary" onClick={() => setEditing(!editing)}>{editing ? 'Cancel' : 'Edit rule'}</button>
        </div>
      )}
      {editing && (
        <div>
          <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} aria-label="Rule expression" spellCheck={false} />
          <p className="meta">
            Conditions: {'{"field": "amount", "op": "gt", "value": 500}'}; combine with {'{"all": [...]}'} or {'{"any": [...]}'}.
            Operators: eq ne gt gte lt lte in not_in contains missing present.
          </p>
          <button type="button" className="button-secondary" onClick={saveExpression}>Save rule</button>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </li>
  )
}

/** Epic 33, US33.1: the process's decision points as executable rules --
 * what lets the rules label cases without a person per case. */
export default function DecisionRulesPanel({ processId, canEdit }: { processId: string; canEdit: boolean }) {
  const [rules, setRules] = useState<DecisionRule[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function load() {
    try {
      setRules(await listDecisionRules(processId))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load rules')
    }
  }

  useEffect(() => {
    void load()
  }, [processId])

  async function draft() {
    setBusy(true)
    setError(null)
    try {
      setRules(await proposeDecisionRules(processId))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not draft rules')
    } finally {
      setBusy(false)
    }
  }

  const gateways = [...new Set(rules.map((r) => r.gateway_id))]

  return (
    <section aria-labelledby="rules-heading">
      <h2 id="rules-heading">Decision rules</h2>
      <p className="meta">
        Each decision point in the process, written as a rule the system can check. Once you confirm them, they can
        label any number of cases with the answer the rules give.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {canEdit && (
        <button type="button" className="button-secondary" onClick={draft} disabled={busy}>
          {busy ? 'Drafting...' : rules.length ? 'Draft rules for new decision points' : 'Draft rules from the diagram'}
        </button>
      )}
      {gateways.map((gatewayId) => (
        <div key={gatewayId}>
          <h3>{rules.find((r) => r.gateway_id === gatewayId)?.gateway_label || gatewayId}</h3>
          <ul className="registry-entry-list">
            {rules.filter((r) => r.gateway_id === gatewayId).map((rule) => (
              <RuleRow key={rule.id} processId={processId} rule={rule} canEdit={canEdit} onSaved={load} />
            ))}
          </ul>
        </div>
      ))}
    </section>
  )
}
