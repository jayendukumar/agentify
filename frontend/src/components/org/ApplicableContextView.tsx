import { useState } from 'react'
import { ApiError, getApplicableContext } from '../../api/client'
import type { ApplicableContext, ContextEntity, ContextKind } from '../../api/types'
import { describeEntity, KIND_LABELS, sourceLabel } from '../../lib/contextLabels'

interface Props {
  businessUnits: ContextEntity[]
}

const ORDER: ContextKind[] = ['regulation', 'obligation', 'control', 'policy', 'approval_authority', 'role', 'application', 'data_class', 'glossary_term']

/** US27.9: "what applies here?" for a chosen business unit and set of
 * jurisdictions -- the same deterministic applicability the context
 * resolver uses. */
export default function ApplicableContextView({ businessUnits }: Props) {
  const [unit, setUnit] = useState('')
  const [jurisdictions, setJurisdictions] = useState('')
  const [dataClasses, setDataClasses] = useState('')
  const [result, setResult] = useState<ApplicableContext | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    setLoading(true)
    setError(null)
    try {
      setResult(
        await getApplicableContext({
          business_unit_id: unit || null,
          jurisdictions: jurisdictions.split(',').map((j) => j.trim()).filter(Boolean),
          data_classes: dataClasses.split(',').map((d) => d.trim()).filter(Boolean),
        }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not work out what applies')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section aria-labelledby="applicable-heading">
      <h2 id="applicable-heading">What applies where</h2>
      <p className="meta">Pick a part of the business to see the rules, roles, and systems that apply to processes there.</p>
      <form className="create-form" onSubmit={handleSubmit}>
        <label>
          Business unit
          <select value={unit} onChange={(e) => setUnit(e.target.value)}>
            <option value="">Whole organization</option>
            {businessUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </label>
        <label>Countries<input value={jurisdictions} onChange={(e) => setJurisdictions(e.target.value)} placeholder="e.g. DE, UK" /></label>
        <label>Data handled<input value={dataClasses} onChange={(e) => setDataClasses(e.target.value)} placeholder="e.g. personal, health" /></label>
        <button type="submit" className="button-primary" disabled={loading}>{loading ? 'Checking...' : 'Show what applies'}</button>
      </form>
      {error && <p className="error" role="alert">{error}</p>}
      {result && (
        <div className="org-applicable">
          {ORDER.filter((k) => result.entities[k]?.length).length === 0 && (
            <p className="empty-state">Nothing specific applies to this scope yet.</p>
          )}
          {ORDER.filter((k) => result.entities[k]?.length).map((k) => (
            <div key={k}>
              <h3>{KIND_LABELS[k].plural} ({result.entities[k]!.length})</h3>
              <ul>
                {result.entities[k]!.map((entity) => (
                  <li key={entity.id}>
                    <strong>{entity.code ? `${entity.code} -- ` : ''}{entity.name}</strong>
                    {describeEntity(entity) && <span className="meta"> {describeEntity(entity)}</span>}
                    <span className="meta"> ({sourceLabel(entity.source)}, v{entity.version})</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
