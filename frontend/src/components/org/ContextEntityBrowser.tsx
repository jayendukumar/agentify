import { useEffect, useState } from 'react'
import {
  ApiError,
  createContextEntity,
  getContextImpact,
  listContextEntities,
  listContextEntityVersions,
  rerunAffectedSuites,
  retireContextEntity,
} from '../../api/client'
import type { ContextEntity, ContextEntityVersion, ContextImpact, ContextKind } from '../../api/types'
import { BROWSABLE_KINDS, describeEntity, describeScope, KIND_LABELS, sourceLabel } from '../../lib/contextLabels'

interface Props {
  businessUnits: ContextEntity[]
  roles: ContextEntity[]
  canEdit: boolean
  refreshKey: number
  onChanged: () => void
}

const TEXT_FIELD: Partial<Record<ContextKind, string>> = {
  business_unit: 'description',
  role: 'description',
  regulation: 'summary',
  obligation: 'text',
  control: 'description',
  policy: 'summary',
  glossary_term: 'definition',
  application: 'description',
  data_class: 'description',
}

function splitList(value: string): string[] {
  return value.split(',').map((v) => v.trim()).filter(Boolean)
}

/** US27.4/US27.5/US27.8: browse, add, and retire context entities, with
 * each entity's version history one click away. */
export default function ContextEntityBrowser({ businessUnits, roles, canEdit, refreshKey, onChanged }: Props) {
  const [kind, setKind] = useState<ContextKind>('control')
  const [entities, setEntities] = useState<ContextEntity[]>([])
  const [history, setHistory] = useState<{ id: string; versions: ContextEntityVersion[] } | null>(null)
  const [impact, setImpact] = useState<ContextImpact | null>(null)
  const [rerunMessage, setRerunMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const [name, setName] = useState('')
  const [code, setCode] = useState('')
  const [text, setText] = useState('')
  const [scopeUnit, setScopeUnit] = useState('')
  const [scopeJurisdictions, setScopeJurisdictions] = useState('')
  const [humanReview, setHumanReview] = useState(false)
  const [roleId, setRoleId] = useState('')
  const [limit, setLimit] = useState('')
  const [currency, setCurrency] = useState('')
  const [synonyms, setSynonyms] = useState('')

  const unitNames = Object.fromEntries(businessUnits.map((u) => [u.id, u.name]))

  async function load() {
    try {
      setEntities(await listContextEntities(kind))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load context')
    }
  }

  useEffect(() => {
    setHistory(null)
    void load()
  }, [kind, refreshKey])

  function buildData(): Record<string, unknown> {
    const data: Record<string, unknown> = {}
    const field = TEXT_FIELD[kind]
    if (field) data[field] = text
    if (kind === 'control') data.requires_human_review = humanReview
    if (kind === 'glossary_term') data.synonyms = splitList(synonyms)
    if (kind === 'application') data.aliases = splitList(synonyms)
    if (kind === 'jurisdiction') data.parent_code = text.trim() || null
    if (kind === 'approval_authority') {
      const role = roles.find((r) => r.id === roleId)
      data.role_id = roleId || null
      data.role_name = role?.name ?? ''
      data.decision_type = name
      data.limit_amount = limit === '' ? null : Number(limit)
      data.currency = currency
    }
    return data
  }

  async function handleAdd(event: React.FormEvent) {
    event.preventDefault()
    if (!name.trim()) return
    setAdding(true)
    setError(null)
    try {
      const role = roles.find((r) => r.id === roleId)
      await createContextEntity({
        kind,
        name: kind === 'approval_authority' && role ? `${role.name}: ${name.trim()}` : name.trim(),
        code: code.trim() || null,
        data: buildData(),
        parent_id: kind === 'approval_authority' && roleId ? roleId : null,
        applies_to: {
          business_units: scopeUnit ? [scopeUnit] : [],
          jurisdictions: splitList(scopeJurisdictions),
        },
      })
      setName('')
      setCode('')
      setText('')
      setSynonyms('')
      setLimit('')
      await load()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add that')
    } finally {
      setAdding(false)
    }
  }

  async function handleRetire(entity: ContextEntity) {
    if (!window.confirm(`Retire "${entity.name}"? It stops applying to new work; its history is kept.`)) return
    try {
      await retireContextEntity(entity.id)
      await load()
      onChanged()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not retire that')
    }
  }

  async function toggleImpact(entity: ContextEntity) {
    setRerunMessage(null)
    if (impact?.entity_id === entity.id) {
      setImpact(null)
      return
    }
    try {
      setImpact(await getContextImpact(entity.id))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not work out the impact')
    }
  }

  async function handleRerun(entityId: string) {
    try {
      const result = await rerunAffectedSuites(entityId)
      setRerunMessage(`Started ${result.regression_suite_ids.length} regression suite run${result.regression_suite_ids.length === 1 ? '' : 's'}.`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the re-runs')
    }
  }

  async function toggleHistory(entity: ContextEntity) {
    if (history?.id === entity.id) {
      setHistory(null)
      return
    }
    try {
      setHistory({ id: entity.id, versions: await listContextEntityVersions(entity.id) })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load history')
    }
  }

  const textLabel = kind === 'jurisdiction' ? 'Part of (code)' : kind === 'glossary_term' ? 'Definition' : 'Description'

  return (
    <section aria-labelledby="entities-heading">
      <h2 id="entities-heading">What the organization knows</h2>
      <label className="org-kind-picker">
        Show
        <select value={kind} onChange={(e) => setKind(e.target.value as ContextKind)}>
          {BROWSABLE_KINDS.map((k) => <option key={k} value={k}>{KIND_LABELS[k].plural}</option>)}
        </select>
      </label>
      {error && <p className="error" role="alert">{error}</p>}

      {entities.length === 0 ? (
        <p className="empty-state">No {KIND_LABELS[kind].plural.toLowerCase()} yet.</p>
      ) : (
        <ul className="registry-entry-list">
          {entities.map((entity) => (
            <li key={entity.id} className="registry-entry-card">
              <div className="registry-entry-header">
                <strong>{entity.name}</strong>
                {entity.code && <span className="meta">{entity.code}</span>}
                <span className="meta">v{entity.version}</span>
                {entity.kind === 'control' && Boolean(entity.data.requires_human_review) && (
                  <span className="badge confidence-medium">Human review</span>
                )}
              </div>
              {describeEntity(entity) && <p>{describeEntity(entity)}</p>}
              <p className="meta">
                {describeScope(entity, unitNames)} · {sourceLabel(entity.source)}
              </p>
              <div className="registry-entry-actions">
                <button type="button" className="button-secondary" onClick={() => toggleHistory(entity)} aria-expanded={history?.id === entity.id}>
                  {history?.id === entity.id ? 'Hide history' : 'History'}
                </button>
                <button type="button" className="button-secondary" onClick={() => toggleImpact(entity)} aria-expanded={impact?.entity_id === entity.id}>
                  {impact?.entity_id === entity.id ? 'Hide impact' : 'What uses this?'}
                </button>
                {canEdit && (
                  <button type="button" className="button-secondary" onClick={() => handleRetire(entity)}>Retire</button>
                )}
              </div>
              {impact?.entity_id === entity.id && (
                <div className="org-impact" role="status">
                  <p>{impact.summary}</p>
                  {canEdit && impact.regression_suite_ids.length > 0 && (
                    <button type="button" className="button-secondary" onClick={() => handleRerun(entity.id)}>
                      Re-run affected regression suites
                    </button>
                  )}
                  {rerunMessage && <p className="info">{rerunMessage}</p>}
                </div>
              )}
              {history?.id === entity.id && (
                <ol className="org-history">
                  {history.versions.map((v) => (
                    <li key={v.version}>
                      <strong>v{v.version}</strong> <span className="meta">{new Date(v.changed_at).toLocaleString()}</span>{' '}
                      {v.change_note && <span>-- {v.change_note}</span>}
                    </li>
                  ))}
                </ol>
              )}
            </li>
          ))}
        </ul>
      )}

      {canEdit && (
        <form className="org-upload-form" onSubmit={handleAdd}>
          <fieldset disabled={adding}>
            <legend>Add {KIND_LABELS[kind].singular.toLowerCase()}</legend>
            {kind === 'approval_authority' ? (
              <>
                <label>
                  Role
                  <select value={roleId} onChange={(e) => setRoleId(e.target.value)} required>
                    <option value="">Choose a role...</option>
                    {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                  </select>
                </label>
                <label>Decision<input value={name} onChange={(e) => setName(e.target.value)} placeholder="refund" required /></label>
                <label>Up to<input type="number" min={0} value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="no limit" /></label>
                <label>Currency<input value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} maxLength={3} /></label>
              </>
            ) : (
              <>
                <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required /></label>
                <label>Code<input value={code} onChange={(e) => setCode(e.target.value)} placeholder={kind === 'jurisdiction' ? 'e.g. DE' : 'optional'} /></label>
                <label>{textLabel}<input value={text} onChange={(e) => setText(e.target.value)} /></label>
                {(kind === 'glossary_term' || kind === 'application') && (
                  <label>{kind === 'application' ? 'Also known as' : 'Synonyms'}<input value={synonyms} onChange={(e) => setSynonyms(e.target.value)} placeholder="comma separated" /></label>
                )}
                {kind === 'control' && (
                  <label className="check-row">
                    <input type="checkbox" checked={humanReview} onChange={(e) => setHumanReview(e.target.checked)} />
                    Requires a person to review
                  </label>
                )}
              </>
            )}
            {kind !== 'business_unit' && kind !== 'jurisdiction' && (
              <>
                <label>
                  Applies to
                  <select value={scopeUnit} onChange={(e) => setScopeUnit(e.target.value)}>
                    <option value="">Whole organization</option>
                    {businessUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                  </select>
                </label>
                <label>In<input value={scopeJurisdictions} onChange={(e) => setScopeJurisdictions(e.target.value)} placeholder="e.g. EU (empty = everywhere)" /></label>
              </>
            )}
            <button type="submit" className="button-primary">{adding ? 'Adding...' : 'Add'}</button>
          </fieldset>
        </form>
      )}
    </section>
  )
}
