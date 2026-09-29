import { useEffect, useState } from 'react'
import {
  ApiError,
  createTwinEnvironment,
  deleteTwinEnvironment,
  listTwinEnvironments,
  updateTwinEnvironment,
} from '../api/client'
import type { FaultConfig, TwinEnvironment } from '../api/types'

function parseJsonField<T>(raw: string, fieldLabel: string): T {
  try {
    return JSON.parse(raw) as T
  } catch {
    throw new Error(`${fieldLabel} is not valid JSON`)
  }
}

const EMPTY_FORM = {
  name: '',
  systemsCsv: '',
  datasetsJson: '{}',
  personasJson: '{}',
  eventsJson: '[]',
  faultsJson: '{}',
}

// Epic 21, US21.1/US21.2/US21.4: a named, versioned, reusable bundle of
// systems/datasets/personas/events plus a per-system fault-injection
// catalog -- a scenario optionally references one by id (see the
// "Environment" selector this panel's caller adds to its own scenario
// form) instead of redeclaring simulation config from scratch.
export default function TwinEnvironmentPanel({ processId }: { processId: string }) {
  const [environments, setEnvironments] = useState<TwinEnvironment[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    listTwinEnvironments(processId)
      .then((list) => {
        if (!cancelled) setEnvironments(list)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : 'Failed to load twin environments')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [processId])

  function startEdit(env: TwinEnvironment) {
    setEditingId(env.id)
    setForm({
      name: env.name,
      systemsCsv: env.systems.join(', '),
      datasetsJson: JSON.stringify(env.datasets, null, 2),
      personasJson: JSON.stringify(env.personas, null, 2),
      eventsJson: JSON.stringify(env.events, null, 2),
      faultsJson: JSON.stringify(env.faults, null, 2),
    })
    setSaveError(null)
  }

  function startCreate() {
    setEditingId('new')
    setForm(EMPTY_FORM)
    setSaveError(null)
  }

  async function handleSave(event: React.FormEvent) {
    event.preventDefault()
    setSaveError(null)
    try {
      const body = {
        name: form.name,
        systems: form.systemsCsv.split(',').map((s) => s.trim()).filter(Boolean),
        datasets: parseJsonField<Record<string, unknown>>(form.datasetsJson, 'Datasets'),
        personas: parseJsonField<Record<string, unknown>>(form.personasJson, 'Personas'),
        events: parseJsonField<Record<string, unknown>[]>(form.eventsJson, 'Events'),
        faults: parseJsonField<Record<string, FaultConfig[]>>(form.faultsJson, 'Faults'),
      }
      setSaving(true)
      if (editingId && editingId !== 'new') {
        const updated = await updateTwinEnvironment(processId, editingId, body)
        setEnvironments((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
      } else {
        const created = await createTwinEnvironment(processId, body)
        setEnvironments((prev) => [...prev, created])
      }
      setEditingId(null)
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : 'Failed to save environment')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete(id: string) {
    if (!window.confirm('Delete this environment? Scenarios referencing it will lose the reference.')) return
    try {
      await deleteTwinEnvironment(processId, id)
      setEnvironments((prev) => prev.filter((e) => e.id !== id))
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : 'Could not delete the environment. Please try again.')
    }
  }

  return (
    <div className="twin-environment-panel" data-testid="twin-environment-panel">
      <h3>Digital twin environments</h3>
      {loadError && <p className="error" role="alert">{loadError}</p>}
      {loading ? (
        <p>Loading...</p>
      ) : environments.length === 0 ? (
        <p className="meta">No reusable environments defined yet.</p>
      ) : (
        <ul className="registry-entry-list">
          {environments.map((env) => (
            <li key={env.id} className="registry-entry-card" data-testid="twin-environment-card">
              <div className="registry-entry-header">
                <span>{env.name}</span>
                <span className="meta">v{env.version}</span>
              </div>
              <p className="meta">
                {env.systems.length} system(s) -- {Object.keys(env.faults).length} with declared fault(s)
              </p>
              <div className="registry-entry-actions">
                <button type="button" onClick={() => startEdit(env)}>
                  Edit
                </button>
                <button type="button" className="button-secondary" onClick={() => handleDelete(env.id)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {editingId === null ? (
        <button type="button" onClick={startCreate}>
          New environment
        </button>
      ) : (
        <form className="twin-scenario-form" onSubmit={handleSave} data-testid="twin-environment-form">
          <label>
            Name
            <input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
          </label>
          <label>
            Systems (comma-separated)
            <input
              value={form.systemsCsv}
              onChange={(e) => setForm((f) => ({ ...f, systemsCsv: e.target.value }))}
              placeholder="CRM API, ERP API"
            />
          </label>
          <label>
            Datasets (JSON)
            <textarea value={form.datasetsJson} onChange={(e) => setForm((f) => ({ ...f, datasetsJson: e.target.value }))} rows={3} />
          </label>
          <label>
            Personas (JSON)
            <textarea value={form.personasJson} onChange={(e) => setForm((f) => ({ ...f, personasJson: e.target.value }))} rows={3} />
          </label>
          <label>
            Events (JSON list)
            <textarea value={form.eventsJson} onChange={(e) => setForm((f) => ({ ...f, eventsJson: e.target.value }))} rows={2} />
          </label>
          <label>
            Faults (JSON, keyed by system name -- e.g.{' '}
            <code>{'{"CRM API": [{"type": "http_500", "probability": 0.3}]}'}</code>)
            <textarea value={form.faultsJson} onChange={(e) => setForm((f) => ({ ...f, faultsJson: e.target.value }))} rows={3} />
          </label>
          <div className="registry-entry-actions">
            <button type="submit" disabled={saving}>
              {saving ? 'Saving...' : 'Save environment'}
            </button>
            <button type="button" className="button-secondary" onClick={() => setEditingId(null)}>
              Cancel
            </button>
          </div>
          {saveError && <p className="error" role="alert">{saveError}</p>}
        </form>
      )}
    </div>
  )
}
