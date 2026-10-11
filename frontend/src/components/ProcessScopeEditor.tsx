import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, listContextEntities, setProcessScope } from '../api/client'
import type { ContextEntity, ProcessScope } from '../api/types'

interface Props {
  processId: string
  scope: ProcessScope
  canEdit: boolean
  onSaved: (scope: ProcessScope) => void
}

function splitList(value: string): string[] {
  return value.split(',').map((v) => v.trim()).filter(Boolean)
}

/** Epic 27, US27.2: "Which part of the business is this?" -- asked once,
 * so everything downstream receives the right context. */
export default function ProcessScopeEditor({ processId, scope, canEdit, onSaved }: Props) {
  const [units, setUnits] = useState<ContextEntity[]>([])
  const [editing, setEditing] = useState(false)
  const [unit, setUnit] = useState(scope.business_unit_id ?? '')
  const [jurisdictions, setJurisdictions] = useState(scope.jurisdictions.join(', '))
  const [dataClasses, setDataClasses] = useState(scope.data_classes.join(', '))
  const [processType, setProcessType] = useState(scope.process_type ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listContextEntities('business_unit').then(setUnits).catch(() => setUnits([]))
  }, [])

  const unitName = units.find((u) => u.id === scope.business_unit_id)?.name
  const isOrgWide = !scope.business_unit_id && scope.jurisdictions.length === 0

  async function handleSave(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const saved = await setProcessScope(processId, {
        business_unit_id: unit || null,
        jurisdictions: splitList(jurisdictions),
        process_type: processType.trim() || null,
        data_classes: splitList(dataClasses),
      })
      onSaved(saved)
      setEditing(false)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the scope')
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <div className="process-scope-summary">
        <span className="meta">Part of the business:</span>{' '}
        <strong>{isOrgWide ? 'Whole organization' : [unitName, scope.jurisdictions.join(', ')].filter(Boolean).join(' · ')}</strong>
        {scope.data_classes.length > 0 && <span className="meta"> · handles {scope.data_classes.join(', ')} data</span>}{' '}
        {canEdit && (
          <button type="button" className="link-button" onClick={() => setEditing(true)}>
            {isOrgWide ? 'Set scope' : 'Change'}
          </button>
        )}
        {isOrgWide && (
          <p className="meta">
            Setting the business unit and countries lets later steps use the rules that apply here.{' '}
            <Link to="/organization">Organization context</Link>
          </p>
        )}
      </div>
    )
  }

  return (
    <form className="create-form" onSubmit={handleSave} aria-label="Process scope">
      <label>
        Business unit
        <select value={unit} onChange={(e) => setUnit(e.target.value)}>
          <option value="">Whole organization</option>
          {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
        </select>
      </label>
      <label>Countries<input value={jurisdictions} onChange={(e) => setJurisdictions(e.target.value)} placeholder="e.g. DE, UK" /></label>
      <label>Data handled<input value={dataClasses} onChange={(e) => setDataClasses(e.target.value)} placeholder="e.g. personal, payment" /></label>
      <label>Process type<input value={processType} onChange={(e) => setProcessType(e.target.value)} placeholder="e.g. onboarding" /></label>
      <button type="submit" className="button-primary" disabled={saving}>{saving ? 'Saving...' : 'Save'}</button>
      <button type="button" className="button-secondary" onClick={() => setEditing(false)}>Cancel</button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  )
}
