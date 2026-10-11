import { useEffect, useState } from 'react'
import {
  ApiError,
  createConnector,
  deleteConnector,
  listConnectorRuns,
  listConnectors,
  listConnectorTypes,
  syncConnector,
} from '../../api/client'
import type { Connector, ConnectorRun, ContextEntity } from '../../api/types'

const CONFIG_EXAMPLES: Record<string, string> = {
  folder: '{"path": "/data/policies"}',
  http: '{"url": "https://cmdb.example.com/api/now/table/cmdb_ci_appl", "kind": "application", "records_key": "result",\n "mapping": {"external_id": "sys_id", "name": "name", "data": {"vendor": "vendor", "aliases": "u_aliases"}},\n "token_env": "CMDB_TOKEN"}',
  oscal: '{"url": "https://raw.githubusercontent.com/usnistgov/oscal-content/main/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json"}',
  scim: '{"url": "https://graph.microsoft.com/rbac/scim/v2", "group_prefix": "AXY-", "token_env": "IDP_SCIM_TOKEN"}',
  mcp: '{"url": "https://grc.example.com/mcp", "tool": "list_controls", "kind": "control",\n "mapping": {"external_id": "id", "name": "title", "data": {"description": "text"}}, "token_env": "GRC_MCP_TOKEN"}',
}

/** Epic 31, US31.2-US31.6, US31.9: read-only connectors to systems of record. */
export default function ConnectorsPanel({ businessUnits, canEdit }: { businessUnits: ContextEntity[]; canEdit: boolean }) {
  const [connectors, setConnectors] = useState<Connector[]>([])
  const [types, setTypes] = useState<{ type: string; description: string }[]>([])
  const [runs, setRuns] = useState<{ id: string; runs: ConnectorRun[] } | null>(null)
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const [name, setName] = useState('')
  const [type, setType] = useState('http')
  const [config, setConfig] = useState(CONFIG_EXAMPLES.http)
  const [schedule, setSchedule] = useState('')
  const [owner, setOwner] = useState('')

  async function load() {
    try {
      const [list, typeList] = await Promise.all([listConnectors(), listConnectorTypes()])
      setConnectors(list)
      setTypes(typeList)
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not load connectors' })
    }
  }

  useEffect(() => {
    void load()
  }, [])

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault()
    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(config)
    } catch {
      setMessage({ kind: 'error', text: 'Settings must be valid JSON.' })
      return
    }
    try {
      await createConnector({
        name: name.trim(),
        type,
        config: parsed,
        enabled: true,
        schedule_minutes: schedule ? Number(schedule) : null,
        owner_business_unit_id: owner || null,
      })
      setName('')
      setMessage({ kind: 'info', text: 'Connector added.' })
      await load()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not add the connector' })
    }
  }

  async function handleSync(connector: Connector) {
    setBusy(connector.id)
    setMessage(null)
    try {
      const result = await syncConnector(connector.id)
      if (result.queued) {
        setMessage({ kind: 'info', text: `${connector.name}: sync queued.` })
      } else if (result.run) {
        const s = result.run.stats
        setMessage({
          kind: result.run.status === 'succeeded' ? 'info' : 'error',
          text:
            result.run.status === 'succeeded'
              ? `${connector.name}: ${s.created ?? 0} added, ${s.updated ?? 0} changed, ${s.retired ?? 0} retired, ${s.unchanged ?? 0} unchanged.`
              : `${connector.name}: ${result.run.error}`,
        })
      }
      await load()
      if (runs?.id === connector.id) setRuns({ id: connector.id, runs: await listConnectorRuns(connector.id) })
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Sync failed' })
    } finally {
      setBusy(null)
    }
  }

  async function toggleRuns(connector: Connector) {
    if (runs?.id === connector.id) {
      setRuns(null)
      return
    }
    setRuns({ id: connector.id, runs: await listConnectorRuns(connector.id) })
  }

  async function handleDelete(connector: Connector) {
    if (!window.confirm(`Remove the "${connector.name}" connector? What it synced stays.`)) return
    await deleteConnector(connector.id)
    await load()
  }

  return (
    <section aria-labelledby="connectors-heading">
      <h2 id="connectors-heading">Connectors</h2>
      <p className="meta">
        Keep context current from the systems you already run. Connectors only ever read; tokens live in environment
        variables, named in the settings as <code>token_env</code>, never stored here.
      </p>
      {message && <p className={message.kind} role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}
      {connectors.length === 0 ? (
        <p className="empty-state">No connectors yet.</p>
      ) : (
        <ul className="registry-entry-list">
          {connectors.map((connector) => (
            <li key={connector.id} className="registry-entry-card">
              <div className="registry-entry-header">
                <strong>{connector.name}</strong>
                <span className="badge">{connector.type}</span>
                {connector.schedule_minutes && <span className="meta">every {connector.schedule_minutes} min</span>}
                {connector.last_status && (
                  <span className={`badge ${connector.last_status === 'succeeded' ? 'status-done' : 'status-failed'}`}>
                    last sync {connector.last_status}
                  </span>
                )}
              </div>
              {connector.last_error && <p className="error">{connector.last_error}</p>}
              <div className="registry-entry-actions">
                <button type="button" className="button-secondary" disabled={!canEdit || busy !== null} onClick={() => handleSync(connector)}>
                  {busy === connector.id ? 'Syncing...' : 'Sync now'}
                </button>
                <button type="button" className="button-secondary" onClick={() => toggleRuns(connector)} aria-expanded={runs?.id === connector.id}>
                  {runs?.id === connector.id ? 'Hide history' : 'Sync history'}
                </button>
                {canEdit && <button type="button" className="button-secondary" onClick={() => handleDelete(connector)}>Remove</button>}
              </div>
              {runs?.id === connector.id && (
                <ol className="org-history">
                  {runs.runs.map((run) => (
                    <li key={run.id}>
                      <strong>{new Date(run.started_at).toLocaleString()}</strong> {run.status}
                      {run.error ? ` -- ${run.error}` : ` -- ${Object.entries(run.stats).filter(([, v]) => v).map(([k, v]) => `${v} ${k.replace('_', ' ')}`).join(', ') || 'no changes'}`}
                      {run.impacts.length > 0 && (
                        <ul>
                          {run.impacts.map((impact) => <li key={impact.entity_id}>{impact.summary}</li>)}
                        </ul>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </li>
          ))}
        </ul>
      )}
      {canEdit && (
        <form className="org-upload-form" onSubmit={handleCreate}>
          <fieldset>
            <legend>Add a connector</legend>
            <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required placeholder="servicenow-cmdb" /></label>
            <label>
              Type
              <select
                value={type}
                onChange={(e) => {
                  setType(e.target.value)
                  setConfig(CONFIG_EXAMPLES[e.target.value] ?? '{}')
                }}
              >
                {types.map((t) => <option key={t.type} value={t.type}>{t.type} -- {t.description}</option>)}
              </select>
            </label>
            <label>
              Settings (JSON)
              <textarea rows={5} value={config} onChange={(e) => setConfig(e.target.value)} spellCheck={false} />
            </label>
            <label>Sync every (minutes)<input type="number" min={5} value={schedule} onChange={(e) => setSchedule(e.target.value)} placeholder="manual only" /></label>
            <label>
              Owned by
              <select value={owner} onChange={(e) => setOwner(e.target.value)}>
                <option value="">Whole organization</option>
                {businessUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </label>
            <button type="submit" className="button-primary">Add connector</button>
          </fieldset>
        </form>
      )}
    </section>
  )
}
