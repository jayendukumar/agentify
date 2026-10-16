// Epic 38, US38.8: the operator console -- the hosting company's view of
// the deployment, on its own host. Tenants, their health and usage as
// counts, and support access where a tenant's admin granted it. Nothing
// here shows a tenant's content.
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import BrandLogo from '../components/BrandLogo'
import '../auth/LoginPage.css'
import { operatorApi, type Operator, type OperatorConfig, type OperatorGrant, type PlatformEvent, type Tenant } from './api'

const SIGN_IN_ERRORS: Record<string, string> = {
  not_an_operator: "Your account isn't in the platform operators' group.",
  mfa_required: 'The platform requires multi-factor authentication. Sign in again with your second factor.',
  state_mismatch: 'That sign-in was started in another browser or tab. Please sign in again.',
  expired: 'That sign-in expired. Please sign in again.',
}

function message(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

// The API's timestamps are UTC without a zone.
function when(value: string | null): string {
  return value ? new Date(value.endsWith('Z') ? value : `${value}Z`).toLocaleString() : '--'
}

function signInError(): string | null {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('auth_error')
  if (!code) return null
  window.history.replaceState(null, '', window.location.pathname)
  return SIGN_IN_ERRORS[code] ?? 'The sign-in was refused.'
}

function SignIn({ config, onSignedIn }: { config: OperatorConfig; onSignedIn: (op: Operator) => void }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(signInError)

  async function submit(event: FormEvent) {
    event.preventDefault()
    try {
      onSignedIn(await operatorApi.login(name, email))
    } catch (err) {
      setError(message(err, 'Could not sign in'))
    }
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <BrandLogo variant="full" className="login-logo" />
        <h1>Operator console</h1>
        <p className="meta">For the hosting company's operators. Tenant data isn't visible here.</p>
        {error && <p className="error" role="alert">{error}</p>}
        {config.auth_mode === 'oidc' && config.sso_login_url && (
          <a className="button-link-primary login-sso-button" href={config.sso_login_url}>Sign in with the platform's SSO</a>
        )}
        {config.auth_mode === 'local' && (
          <form className="login-form" onSubmit={submit}>
            <p className="meta">Local sign-in (development only).</p>
            <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required /></label>
            <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <button type="submit">Sign in</button>
          </form>
        )}
        {config.auth_mode === 'none' && <p className="meta">The operator console isn't set up.</p>}
      </div>
    </div>
  )
}

function SupportAccess({ tenant }: { tenant: Tenant }) {
  const [grants, setGrants] = useState<OperatorGrant[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    operatorApi.grants(tenant.id).then(setGrants).catch((err) => setError(message(err, 'Could not load support grants')))
  }, [tenant.id])

  async function enter(grant: OperatorGrant) {
    try {
      const { url } = await operatorApi.enter(tenant.id, grant.id)
      window.location.assign(url)
    } catch (err) {
      setError(message(err, 'Could not open support access'))
    }
  }

  if (error) return <p className="error" role="alert">{error}</p>
  if (grants === null) return null
  if (grants.length === 0) return <p className="meta">No support access granted to you. The tenant's admin grants it on their Directory page.</p>
  return (
    <ul>
      {grants.map((grant) => (
        <li key={grant.id}>
          {grant.access === 'read' ? 'Read-only' : 'Can edit'}, until {when(grant.expires_at)}, granted by {grant.granted_by}:{' '}
          <em>{grant.reason}</em>{' '}
          <button type="button" className="button-secondary" onClick={() => enter(grant)}>Open as support</button>
        </li>
      ))}
    </ul>
  )
}

function CreateTenant({ onCreated }: { onCreated: () => void }) {
  const [form, setForm] = useState({ slug: '', display_name: '', host: '', region: '' })
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await operatorApi.createTenant({ ...form, region: form.region || null })
      setForm({ slug: '', display_name: '', host: '', region: '' })
      onCreated()
    } catch (err) {
      setError(message(err, 'Could not create the tenant'))
    }
  }

  return (
    <form className="identity-form" onSubmit={submit} aria-label="Create a tenant">
      <label>Slug<input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} placeholder="acme" required /></label>
      <label>Name<input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="Acme Corp" /></label>
      <label>Host<input value={form.host} onChange={(e) => setForm({ ...form, host: e.target.value })} placeholder="acme.localhost" required /></label>
      <label>Region<input value={form.region} onChange={(e) => setForm({ ...form, region: e.target.value })} placeholder="optional" /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="registry-entry-actions"><button type="submit">Create tenant</button></div>
    </form>
  )
}

function Console({ operator, onSignedOut }: { operator: Operator; onSignedOut: () => void }) {
  const [tenants, setTenants] = useState<Tenant[] | null>(null)
  const [events, setEvents] = useState<PlatformEvent[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    operatorApi.tenants().then(setTenants).catch((err) => setError(message(err, 'Could not load tenants')))
    operatorApi.audit().then(setEvents).catch(() => setEvents([]))
  }, [])
  useEffect(load, [load])

  async function change(tenant: Tenant, action: 'suspend' | 'activate' | 'delete') {
    setError(null)
    setConfirmDelete(null)
    try {
      await operatorApi.changeStatus(tenant.id, action)
      load()
    } catch (err) {
      setError(message(err, `Could not ${action} ${tenant.slug}`))
    }
  }

  async function signOut() {
    const { logout_url } = await operatorApi.logout()
    if (logout_url) window.location.assign(logout_url)
    else onSignedOut()
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-title-link">
          <BrandLogo variant="full" tone="white" className="app-header-logo" />
          <span className="app-header-tagline">Operator console</span>
        </span>
        <span className="app-header-user">
          <span className="app-header-user-name">{operator.name}</span>
          <button type="button" className="button-secondary" onClick={signOut}>Log out</button>
        </span>
      </header>
      <main className="page" id="main-content">
        <h1>Tenants</h1>
        <p className="meta">Usage is counts only. To look inside a tenant, its admin grants support access.</p>
        {error && <p className="error" role="alert">{error}</p>}
        {tenants && (
          <table className="data-table">
            <thead>
              <tr>
                <th>Tenant</th><th>Status</th><th>Hosts</th><th>Users</th><th>Processes</th><th>Jobs waiting</th>
                <th>Last sign-in</th><th>Support</th><th />
              </tr>
            </thead>
            <tbody>
              {tenants.map((tenant) => (
                <tr key={tenant.id}>
                  <td>{tenant.display_name} <span className="meta">({tenant.slug})</span></td>
                  <td><span className="badge">{tenant.status}</span></td>
                  <td>{tenant.hosts.join(', ')}</td>
                  <td>{tenant.usage?.users ?? '--'}</td>
                  <td>{tenant.usage?.processes ?? '--'}</td>
                  <td>{tenant.usage?.jobs_waiting ?? '--'}</td>
                  <td>{when(tenant.usage?.last_sign_in_at ?? null)}</td>
                  <td>
                    {tenant.status === 'active' && (
                      <button type="button" className="button-secondary" onClick={() => setOpen(open === tenant.id ? null : tenant.id)}>
                        {tenant.usage?.support_grants_active ? `${tenant.usage.support_grants_active} grant(s)` : 'Support'}
                      </button>
                    )}
                  </td>
                  <td>
                    {tenant.status === 'active' && <button type="button" className="button-secondary" onClick={() => change(tenant, 'suspend')}>Suspend</button>}
                    {tenant.status === 'suspended' && (
                      <>
                        <button type="button" className="button-secondary" onClick={() => change(tenant, 'activate')}>Activate</button>{' '}
                        {confirmDelete === tenant.id ? (
                          <button type="button" className="button-secondary" onClick={() => change(tenant, 'delete')}>
                            Confirm: delete {tenant.slug}
                          </button>
                        ) : (
                          <button type="button" className="button-secondary" onClick={() => setConfirmDelete(tenant.id)}>Delete</button>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {open && tenants?.find((t) => t.id === open) && (
          <section aria-label="Support access">
            <h2>Support access: {tenants.find((t) => t.id === open)!.display_name}</h2>
            <SupportAccess tenant={tenants.find((t) => t.id === open)!} />
          </section>
        )}
        <section>
          <h2>Create a tenant</h2>
          <CreateTenant onCreated={load} />
        </section>
        <section>
          <h2>Platform audit log</h2>
          <ul>
            {events.slice(0, 50).map((event) => (
              <li key={event.id}>
                {when(event.occurred_at)} -- {event.operator_name}: {event.action}
                {event.outcome !== 'success' && ` (${event.outcome})`}
                {event.tenant_id && <span className="meta"> [{event.tenant_id}]</span>}
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  )
}

export default function OperatorConsole() {
  const [config, setConfig] = useState<OperatorConfig | null>(null)
  const [operator, setOperator] = useState<Operator | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      operatorApi.config().then(setConfig),
      operatorApi.me().then(setOperator).catch(() => setOperator(null)),
    ]).finally(() => setLoading(false))
  }, [])

  if (loading) return <p className="loading-state" role="status">Loading the operator console...</p>
  if (!operator) return config ? <SignIn config={config} onSignedIn={setOperator} /> : null
  return <Console operator={operator} onSignedOut={() => setOperator(null)} />
}
