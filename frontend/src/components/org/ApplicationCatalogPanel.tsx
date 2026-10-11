import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  attachApiSpec,
  createContextEntity,
  getThirdPartyRegister,
  importApplicationsCsv,
  listContextEntities,
  setApplicationEndpoints,
  updateContextEntity,
} from '../../api/client'
import type {
  ApplicationEndpoint,
  ApplicationImportResult,
  Classification,
  ContextEntity,
  EndpointEnvironment,
  ThirdPartyRegisterRow,
} from '../../api/types'

const CLASSIFICATIONS: Classification[] = ['Public', 'Internal', 'Confidential', 'Confidential-PII', 'Restricted']
const ENVIRONMENTS: EndpointEnvironment[] = ['sandbox', 'test', 'dev', 'staging', 'mock', 'production']

interface Props {
  canEdit: boolean
  onChanged: () => void
}

function ApplicationCard({
  app,
  assets,
  canEdit,
  onChanged,
}: {
  app: ContextEntity
  assets: ContextEntity[]
  canEdit: boolean
  onChanged: () => void
}) {
  const data = app.data as {
    aliases?: string[]
    vendor?: string
    criticality?: string | null
    hosting_region?: string
    data_assets?: string[]
    api_title?: string
    api_version?: string
    api_operations?: { operation_id: string }[]
    endpoints?: ApplicationEndpoint[]
  }
  const [open, setOpen] = useState(false)
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)
  const [endpointUrl, setEndpointUrl] = useState('')
  const [endpointEnv, setEndpointEnv] = useState<EndpointEnvironment>('sandbox')
  const specRef = useRef<HTMLInputElement>(null)
  const assetNames = (data.data_assets ?? []).map((id) => assets.find((a) => a.id === id)?.name ?? id)

  async function run(action: () => Promise<unknown>, success: string) {
    setMessage(null)
    try {
      await action()
      setMessage({ kind: 'info', text: success })
      onChanged()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'That did not work' })
    }
  }

  function toggleAsset(assetId: string, checked: boolean) {
    const next = checked ? [...(data.data_assets ?? []), assetId] : (data.data_assets ?? []).filter((id) => id !== assetId)
    void run(() => updateContextEntity(app.id, { data: { ...app.data, data_assets: next } }), 'Data assets updated.')
  }

  function addEndpoint(event: React.FormEvent) {
    event.preventDefault()
    if (!endpointUrl.trim()) return
    const next = [...(data.endpoints ?? []), { url: endpointUrl.trim(), environment: endpointEnv }]
    void run(() => setApplicationEndpoints(app.id, next), 'Endpoint added.').then(() => setEndpointUrl(''))
  }

  function removeEndpoint(index: number) {
    const next = (data.endpoints ?? []).filter((_, i) => i !== index)
    void run(() => setApplicationEndpoints(app.id, next), 'Endpoint removed.')
  }

  function uploadSpec() {
    const file = specRef.current?.files?.[0]
    if (!file) return
    void run(() => attachApiSpec(app.id, file), 'API spec attached -- twin tests will use its real operations.')
  }

  return (
    <li className="registry-entry-card">
      <div className="registry-entry-header">
        <strong>{app.name}</strong>
        {data.vendor && <span className="meta">{data.vendor}</span>}
        {data.criticality && <span className="badge">{data.criticality}</span>}
        {data.api_operations?.length ? (
          <span className="badge status-done">API spec: {data.api_title || 'attached'} ({data.api_operations.length} operations)</span>
        ) : (
          <span className="badge">No API spec</span>
        )}
      </div>
      <p className="meta">
        {(data.aliases ?? []).length > 0 && `Also known as ${(data.aliases ?? []).join(', ')} · `}
        {assetNames.length > 0 ? `Holds ${assetNames.join(', ')}` : 'No data assets recorded'}
        {data.hosting_region && ` · Hosted in ${data.hosting_region}`}
      </p>
      <button type="button" className="button-secondary" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? 'Close' : 'Details'}
      </button>
      {open && (
        <div className="org-app-details">
          <fieldset disabled={!canEdit}>
            <legend>Data it holds</legend>
            {assets.length === 0 && <p className="meta">Add data assets below first.</p>}
            {assets.map((asset) => (
              <label key={asset.id} className="check-row">
                <input
                  type="checkbox"
                  checked={(data.data_assets ?? []).includes(asset.id)}
                  onChange={(e) => toggleAsset(asset.id, e.target.checked)}
                />
                {asset.name} <span className="meta">({String(asset.data.classification ?? 'Internal')})</span>
              </label>
            ))}
          </fieldset>
          <fieldset disabled={!canEdit}>
            <legend>API spec (OpenAPI, JSON or YAML)</legend>
            <input ref={specRef} type="file" accept=".json,.yaml,.yml" aria-label={`OpenAPI spec for ${app.name}`} />
            <button type="button" className="button-secondary" onClick={uploadSpec}>Attach spec</button>
          </fieldset>
          <fieldset disabled={!canEdit}>
            <legend>Endpoints</legend>
            <p className="meta">
              Twin tests in API mode only ever call sandbox, test, dev, staging, or mock endpoints on recognisably
              non-production hosts. Production endpoints can be recorded for reference but are never called.
            </p>
            <ul>
              {(data.endpoints ?? []).map((endpoint, index) => (
                <li key={`${endpoint.url}-${index}`}>
                  <code>{endpoint.url}</code> <span className={`badge ${endpoint.environment === 'production' ? 'confidence-low' : ''}`}>{endpoint.environment}</span>{' '}
                  <button type="button" className="link-button" onClick={() => removeEndpoint(index)}>Remove</button>
                </li>
              ))}
            </ul>
            <form className="create-form" onSubmit={addEndpoint}>
              <label>URL<input value={endpointUrl} onChange={(e) => setEndpointUrl(e.target.value)} placeholder="https://api.sandbox.example.com" /></label>
              <label>
                Environment
                <select value={endpointEnv} onChange={(e) => setEndpointEnv(e.target.value as EndpointEnvironment)}>
                  {ENVIRONMENTS.map((env) => <option key={env} value={env}>{env}</option>)}
                </select>
              </label>
              <button type="submit" className="button-secondary">Add endpoint</button>
            </form>
          </fieldset>
        </div>
      )}
      {message && <p className={message.kind} role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}
    </li>
  )
}

/** Epic 29: applications, data assets, CSV import, and the third-party
 * register. */
export default function ApplicationCatalogPanel({ canEdit, onChanged }: Props) {
  const [apps, setApps] = useState<ContextEntity[]>([])
  const [assets, setAssets] = useState<ContextEntity[]>([])
  const [register, setRegister] = useState<ThirdPartyRegisterRow[]>([])
  const [preview, setPreview] = useState<ApplicationImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [assetName, setAssetName] = useState('')
  const [assetClass, setAssetClass] = useState<Classification>('Internal')
  const [assetResidency, setAssetResidency] = useState('')
  const csvRef = useRef<HTMLInputElement>(null)

  async function load() {
    try {
      const [appList, assetList, registerRows] = await Promise.all([
        listContextEntities('application'),
        listContextEntities('data_asset'),
        getThirdPartyRegister(),
      ])
      setApps(appList)
      setAssets(assetList)
      setRegister(registerRows)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the catalog')
    }
  }

  useEffect(() => {
    void load()
  }, [])

  function changed() {
    void load()
    onChanged()
  }

  async function handlePreview() {
    const file = csvRef.current?.files?.[0]
    if (!file) {
      setError('Choose a CSV file to import.')
      return
    }
    setError(null)
    setInfo(null)
    try {
      setPreview(await importApplicationsCsv(file, true))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not read the CSV')
    }
  }

  async function handleImport() {
    const file = csvRef.current?.files?.[0]
    if (!file) return
    try {
      const result = await importApplicationsCsv(file, false)
      setPreview(null)
      setInfo(`Imported: ${result.created} added, ${result.updated} updated.`)
      if (csvRef.current) csvRef.current.value = ''
      changed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Import failed')
    }
  }

  async function handleAddAsset(event: React.FormEvent) {
    event.preventDefault()
    if (!assetName.trim()) return
    try {
      await createContextEntity({
        kind: 'data_asset',
        name: assetName.trim(),
        data: {
          classification: assetClass,
          residency: assetResidency.split(',').map((r) => r.trim()).filter(Boolean),
        },
      })
      setAssetName('')
      setAssetResidency('')
      changed()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not add the data asset')
    }
  }

  return (
    <section aria-labelledby="catalog-heading">
      <h2 id="catalog-heading">Applications</h2>
      <p className="meta">
        One record per real system, with every name people use for it. Process steps and agents that mention any of
        those names link to it, so twin tests use its real API and agents get permissions that match its data.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {info && <p className="info" role="status">{info}</p>}

      {canEdit && (
        <div className="org-upload-form">
          <fieldset>
            <legend>Import from CSV</legend>
            <p className="meta">An export from your CMDB, architecture tool, or a spreadsheet. Needs a name column; aliases, vendor, owner, region, and criticality are picked up when present.</p>
            <input ref={csvRef} type="file" accept=".csv" aria-label="Applications CSV" />
            <button type="button" className="button-secondary" onClick={handlePreview}>Preview</button>
          </fieldset>
        </div>
      )}
      {preview && (
        <div className="org-import-preview">
          <table>
            <thead><tr><th>Row</th><th>Name</th><th>What will happen</th></tr></thead>
            <tbody>
              {preview.rows.map((row) => (
                <tr key={row.row}>
                  <td>{row.row}</td>
                  <td>{row.name || <span className="meta">(blank)</span>}</td>
                  <td>{row.action}{row.message ? ` -- ${row.message}` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" className="button-primary" onClick={handleImport}>Import</button>{' '}
          <button type="button" className="button-secondary" onClick={() => setPreview(null)}>Cancel</button>
        </div>
      )}

      {apps.length === 0 ? (
        <p className="empty-state">No applications yet. Import a CSV, add them in the profile wizard, or add one under Browse context.</p>
      ) : (
        <ul className="registry-entry-list">
          {apps.map((app) => <ApplicationCard key={app.id} app={app} assets={assets} canEdit={canEdit} onChanged={changed} />)}
        </ul>
      )}

      <h2>Data assets</h2>
      {assets.length === 0 ? (
        <p className="empty-state">No data assets yet.</p>
      ) : (
        <ul>
          {assets.map((asset) => (
            <li key={asset.id}>
              <strong>{asset.name}</strong> <span className="badge">{String(asset.data.classification ?? 'Internal')}</span>
              {((asset.data.residency as string[]) ?? []).length > 0 && <span className="meta"> stored in {((asset.data.residency as string[]) ?? []).join(', ')}</span>}
            </li>
          ))}
        </ul>
      )}
      {canEdit && (
        <form className="create-form" onSubmit={handleAddAsset}>
          <label>Data asset<input value={assetName} onChange={(e) => setAssetName(e.target.value)} placeholder="Customer" /></label>
          <label>
            Classification
            <select value={assetClass} onChange={(e) => setAssetClass(e.target.value as Classification)}>
              {CLASSIFICATIONS.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label>Stored in<input value={assetResidency} onChange={(e) => setAssetResidency(e.target.value)} placeholder="EU" /></label>
          <button type="submit" className="button-secondary">Add data asset</button>
        </form>
      )}

      <h2>Third-party register</h2>
      <p className="meta">Which agents depend on which applications and vendors.</p>
      {register.length === 0 ? (
        <p className="empty-state">Nothing to show yet.</p>
      ) : (
        <table className="org-register">
          <thead>
            <tr><th>Application</th><th>Vendor</th><th>Criticality</th><th>Data</th><th>Agents</th><th>Processes</th></tr>
          </thead>
          <tbody>
            {register.map((row) => (
              <tr key={row.application_id}>
                <td>{row.application}</td>
                <td>{row.vendor || '--'}</td>
                <td>{row.criticality ?? '--'}</td>
                <td>{row.classifications.join(', ') || '--'}</td>
                <td>{row.agents.map((a) => a.name).join(', ') || '--'}</td>
                <td>{row.processes.map((p) => p.name).join(', ') || '--'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
