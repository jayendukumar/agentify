import { useEffect, useState } from 'react'
import {
  ApiError,
  listAuditLog,
  listContextChanges,
  listMembers,
  rerunAffectedSuites,
  setMemberships,
  setOrganizationLlmPolicy,
  updateContextEntity,
} from '../../api/client'
import type { AccessLogEntry, ContextChange, ContextEntity, LlmPolicy, MemberAccess, Organization } from '../../api/types'

const EMPTY_POLICY: LlmPolicy = {
  allowed_providers: [],
  residency: [],
  base_url: null,
  api_key_env: null,
  provider_name: null,
  provider_region: null,
  model: null,
  redact_pii: false,
}

function splitList(value: string): string[] {
  return value.split(',').map((v) => v.trim()).filter(Boolean)
}

function PolicyForm({ policy, onSave, label }: { policy: LlmPolicy; onSave: (p: LlmPolicy) => Promise<void>; label: string }) {
  const [residency, setResidency] = useState(policy.residency.join(', '))
  const [providers, setProviders] = useState(policy.allowed_providers.join(', '))
  const [baseUrl, setBaseUrl] = useState(policy.base_url ?? '')
  const [region, setRegion] = useState(policy.provider_region ?? '')
  const [model, setModel] = useState(policy.model ?? '')
  const [keyEnv, setKeyEnv] = useState(policy.api_key_env ?? '')
  const [redactPii, setRedactPii] = useState(policy.redact_pii)
  const [status, setStatus] = useState<string | null>(null)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    try {
      await onSave({
        allowed_providers: splitList(providers),
        residency: splitList(residency),
        base_url: baseUrl.trim() || null,
        api_key_env: keyEnv.trim() || null,
        provider_name: baseUrl.trim() ? 'self_hosted' : null,
        provider_region: region.trim() || null,
        model: model.trim() || null,
        redact_pii: redactPii,
      })
      setStatus('Saved.')
    } catch (err) {
      setStatus(err instanceof ApiError ? err.message : 'Could not save')
    }
  }

  return (
    <form className="org-upload-form" onSubmit={handleSubmit} aria-label={label}>
      <fieldset>
        <legend>{label}</legend>
        <label>Models must be hosted in<input value={residency} onChange={(e) => setResidency(e.target.value)} placeholder="e.g. EU (empty = anywhere)" /></label>
        <label>Allowed providers<input value={providers} onChange={(e) => setProviders(e.target.value)} placeholder="e.g. openrouter, self_hosted (empty = any)" /></label>
        <label>Self-hosted endpoint (OpenAI-compatible)<input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://llm.internal.example/v1" /></label>
        <label>Its hosting region<input value={region} onChange={(e) => setRegion(e.target.value)} placeholder="EU" /></label>
        <label>Model<input value={model} onChange={(e) => setModel(e.target.value)} placeholder="default" /></label>
        <label>API key environment variable<input value={keyEnv} onChange={(e) => setKeyEnv(e.target.value)} placeholder="INTERNAL_LLM_KEY" /></label>
        <label className="check-row">
          <input type="checkbox" checked={redactPii} onChange={(e) => setRedactPii(e.target.checked)} />
          Remove personal data (emails, phone numbers, account and ID numbers) before calling the model
        </label>
        <button type="submit" className="button-secondary">Save model policy</button>
        {status && <p className="meta" role="status">{status}</p>}
      </fieldset>
    </form>
  )
}

/** Epic 31, US31.7-US31.10: who stewards what, model policy, recent
 * changes and their impact, and the context access log. */
export default function GovernancePanel({
  organization,
  businessUnits,
  canEdit,
  onChanged,
}: {
  organization: Organization
  businessUnits: ContextEntity[]
  canEdit: boolean
  onChanged: () => void
}) {
  const [members, setMembers] = useState<MemberAccess[]>([])
  const [changes, setChanges] = useState<ContextChange[]>([])
  const [log, setLog] = useState<AccessLogEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)

  async function load() {
    try {
      const [memberList, changeList] = await Promise.all([listMembers(), listContextChanges()])
      setMembers(memberList)
      setChanges(changeList)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load governance')
    }
    try {
      setLog(await listAuditLog())
    } catch {
      setLog(null) // only group-level users may read the log
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function toggleMembership(member: MemberAccess, unitId: string, field: 'member' | 'steward', value: boolean) {
    let next = member.memberships.filter((m) => m.business_unit_id !== unitId)
    const current = member.memberships.find((m) => m.business_unit_id === unitId)
    if (field === 'member' && value) next = [...next, { business_unit_id: unitId, steward: current?.steward ?? false }]
    if (field === 'steward') next = [...next, { business_unit_id: unitId, steward: value }]
    try {
      await setMemberships(member.user_id, next)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not update membership')
    }
  }

  async function rerun(change: ContextChange) {
    try {
      const result = await rerunAffectedSuites(change.entity_id)
      setInfo(`Started ${result.regression_suite_ids.length} regression suite run(s) for ${change.name}.`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the re-runs')
    }
  }

  const orgPolicy = { ...EMPTY_POLICY, ...((organization.profile.llm_policy as Partial<LlmPolicy>) ?? {}) }

  return (
    <section aria-labelledby="governance-heading">
      {error && <p className="error" role="alert">{error}</p>}
      {info && <p className="info" role="status">{info}</p>}

      <h2 id="governance-heading">Recent changes</h2>
      {changes.length === 0 ? (
        <p className="empty-state">No changes to existing context yet.</p>
      ) : (
        <ul className="registry-entry-list">
          {changes.map((change) => (
            <li key={`${change.entity_id}-${change.version}`} className="registry-entry-card">
              <div className="registry-entry-header">
                <strong>{change.name} v{change.version}</strong>
                <span className="meta">{new Date(change.changed_at).toLocaleString()}</span>
              </div>
              <p>{change.impact.summary}{change.change_note ? ` -- ${change.change_note}` : ''}</p>
              {canEdit && change.impact.regression_suite_ids.length > 0 && (
                <button type="button" className="button-secondary" onClick={() => rerun(change)}>Review and re-run affected tests</button>
              )}
            </li>
          ))}
        </ul>
      )}

      <h2>Who stewards what</h2>
      <p className="meta">
        People with no business unit see and edit everything. Give someone units to limit them to those units'
        context; make them a steward to let them confirm and change it.
      </p>
      {businessUnits.length === 0 ? (
        <p className="empty-state">Add business units first.</p>
      ) : (
        <table className="org-register">
          <thead>
            <tr>
              <th>Person</th>
              {businessUnits.map((u) => <th key={u.id}>{u.name}</th>)}
            </tr>
          </thead>
          <tbody>
            {members.map((member) => (
              <tr key={member.user_id}>
                <td>{member.name} <span className="meta">({member.role}{member.memberships.length === 0 ? ', all units' : ''})</span></td>
                {businessUnits.map((unit) => {
                  const m = member.memberships.find((x) => x.business_unit_id === unit.id)
                  return (
                    <td key={unit.id}>
                      <label className="check-row">
                        <input type="checkbox" disabled={!canEdit} checked={Boolean(m)} onChange={(e) => toggleMembership(member, unit.id, 'member', e.target.checked)} />
                        member
                      </label>
                      <label className="check-row">
                        <input type="checkbox" disabled={!canEdit || !m} checked={Boolean(m?.steward)} onChange={(e) => toggleMembership(member, unit.id, 'steward', e.target.checked)} />
                        steward
                      </label>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h2>Model policy</h2>
      <p className="meta">Where this organization's context may be sent. A business unit's own policy overrides this one.</p>
      {canEdit && <PolicyForm label="Organization-wide model policy" policy={orgPolicy} onSave={async (p) => { await setOrganizationLlmPolicy(p); onChanged() }} />}
      {canEdit &&
        businessUnits.map((unit) => (
          <PolicyForm
            key={unit.id}
            label={`${unit.name} model policy`}
            policy={{ ...EMPTY_POLICY, ...((unit.data.llm_policy as Partial<LlmPolicy>) ?? {}) }}
            onSave={async (p) => { await updateContextEntity(unit.id, { data: { ...unit.data, llm_policy: p } }); onChanged() }}
          />
        ))}

      {log !== null && (
        <>
          <h2>Context access log</h2>
          <table className="org-register">
            <thead><tr><th>When</th><th>Who / what</th><th>Action</th><th>Items</th></tr></thead>
            <tbody>
              {log.map((entry) => (
                <tr key={entry.id}>
                  <td>{new Date(entry.at).toLocaleString()}</td>
                  <td>{entry.actor}</td>
                  <td>{entry.action.replace(/_/g, ' ')}</td>
                  <td>{entry.entity_ids.length || (entry.pack_id ? 'pack' : '')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}
