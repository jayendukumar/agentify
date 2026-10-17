// Epic 38, US38.8: the operator console -- the hosting company's view of
// the deployment, on its own host. Tenants, their health and usage as
// counts, and support access where a tenant's admin granted it. Nothing
// here shows a tenant's content.
import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { ApiError } from '../api/client'
import BrandLogo from '../components/BrandLogo'
import '../auth/LoginPage.css'
import {
  operatorApi,
  type DeletionCertificate,
  type Operator,
  type OperatorConfig,
  type OperatorGrant,
  type PlatformEvent,
  type StatusAction,
  type Tenant,
  type TenantCreated,
  type TenantLimits,
} from './api'

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

function usd(value: number): string {
  return `$${value.toFixed(2)}`
}

const LIMIT_FIELDS: { key: keyof TenantLimits; label: string; step: string }[] = [
  { key: 'max_concurrent_jobs', label: 'Concurrent jobs', step: '1' },
  { key: 'llm_requests_per_minute', label: 'LLM requests per minute', step: '1' },
  { key: 'llm_monthly_budget_usd', label: 'Monthly LLM budget (USD)', step: '0.01' },
]

// US38.9: what a tenant may use. Empty takes the deployment's default.
function Limits({ tenant, onSaved }: { tenant: Tenant; onSaved: () => void }) {
  const toForm = (limits: TenantLimits) =>
    Object.fromEntries(LIMIT_FIELDS.map(({ key }) => [key, limits[key] === null ? '' : String(limits[key])])) as Record<
      keyof TenantLimits,
      string
    >
  const [form, setForm] = useState(() => toForm(tenant.limits))
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    const limits = Object.fromEntries(
      LIMIT_FIELDS.map(({ key }) => [key, form[key].trim() === '' ? null : Number(form[key])]),
    ) as unknown as TenantLimits
    try {
      await operatorApi.setLimits(tenant.id, limits)
      onSaved()
    } catch (err) {
      setError(message(err, 'Could not save the limits'))
    }
  }

  return (
    <form className="identity-form" onSubmit={submit} aria-label="Tenant limits">
      {LIMIT_FIELDS.map(({ key, label, step }) => (
        <label key={key}>
          {label}
          <input
            type="number"
            min={step === '1' ? 1 : 0.01}
            step={step}
            value={form[key]}
            onChange={(e) => setForm({ ...form, [key]: e.target.value })}
            placeholder={tenant.effective_limits[key] === null ? 'no limit' : `default: ${tenant.effective_limits[key]}`}
          />
        </label>
      ))}
      <p className="meta">
        Empty takes the deployment's default. The budget warns the tenant at 80% and stops its LLM calls at 100%.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="registry-entry-actions"><button type="submit">Save limits</button></div>
    </form>
  )
}

const FIRST_ADMIN_TEXT: Record<string, string> = {
  invited: 'Invited',
  expired: 'Invitation expired',
  joined: 'Joined',
  sso: 'Through its IdP',
  none: 'No admin yet',
}

// US38.12: the first admin's one-time link, shown once -- send it to them.
function InvitationLink({ created }: { created: TenantCreated }) {
  if (!created.invitation_url) return null
  return (
    <div className="support-banner" role="status" aria-label="Invitation link">
      Send this link to {created.tenant.first_admin?.email ?? 'the first admin'}. It works once, until{' '}
      {when(created.invitation_expires_at)}, and isn't shown again:{' '}
      <input readOnly value={created.invitation_url} onFocus={(e) => e.currentTarget.select()} aria-label="Invitation URL" />
    </div>
  )
}

const NEW_IDP = {
  issuer: '', discovery_url: '', client_id: '', client_secret_ref: 'secret://tenant/oidc_client_secret',
  display_name: 'single sign-on', acr_values: '', mfa_acr_values: [] as string[], admin_group: '',
}

function CreateTenant({ onCreated }: { onCreated: (created: TenantCreated) => void }) {
  const [form, setForm] = useState({ slug: '', display_name: '', host: '', region: '' })
  const [how, setHow] = useState<'invite' | 'idp'>('invite')
  const [email, setEmail] = useState('')
  const [idp, setIdp] = useState(NEW_IDP)
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      const created = await operatorApi.createTenant({
        ...form,
        region: form.region || null,
        ...(how === 'invite' ? { first_admin_email: email.trim() } : { idp }),
      })
      setForm({ slug: '', display_name: '', host: '', region: '' })
      setEmail('')
      setIdp(NEW_IDP)
      onCreated(created)
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
      <fieldset>
        <legend>First admin</legend>
        <label className="check-row">
          <input type="radio" name="first-admin" checked={how === 'invite'} onChange={() => setHow('invite')} />
          Invite them by email: a one-time link (24 hours) to set up single sign-on
        </label>
        <label className="check-row">
          <input type="radio" name="first-admin" checked={how === 'idp'} onChange={() => setHow('idp')} />
          Connect their identity provider: its admins group gets Admin
        </label>
      </fieldset>
      {how === 'invite' ? (
        <label>First admin's email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      ) : (
        <>
          <label>Issuer<input value={idp.issuer} onChange={(e) => setIdp({ ...idp, issuer: e.target.value })} required /></label>
          <label>Discovery URL (optional)<input value={idp.discovery_url} onChange={(e) => setIdp({ ...idp, discovery_url: e.target.value })} /></label>
          <label>Client ID<input value={idp.client_id} onChange={(e) => setIdp({ ...idp, client_id: e.target.value })} required /></label>
          <label>Client secret reference<input value={idp.client_secret_ref} onChange={(e) => setIdp({ ...idp, client_secret_ref: e.target.value })} required /></label>
          <label>Ask the IdP for (acr_values)<input value={idp.acr_values} onChange={(e) => setIdp({ ...idp, acr_values: e.target.value, mfa_acr_values: e.target.value.split(/[\s,]+/).filter(Boolean) })} placeholder="e.g. mfa" /></label>
          <label>Admins group<input value={idp.admin_group} onChange={(e) => setIdp({ ...idp, admin_group: e.target.value })} placeholder="acme-admins" required /></label>
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="registry-entry-actions"><button type="submit">Create tenant</button></div>
    </form>
  )
}

// US38.13: whether the admin took their data before the tenant goes.
function exportText(tenant: Tenant): string {
  const last = tenant.last_export
  if (!last) return 'No data export made'
  if (last.status === 'downloaded') return `Data export downloaded ${when(last.downloaded_at)}`
  return `Data export ${last.status} (${when(last.created_at)})`
}

function erasable(tenant: Tenant): boolean {
  return tenant.status === 'deleting' && tenant.delete_after !== null && new Date(`${tenant.delete_after}Z`) <= new Date()
}

// US38.13: erasing is final; the operator types the slug to confirm.
function Erase({ tenant, onErased }: { tenant: Tenant; onErased: (cert: DeletionCertificate) => void }) {
  const [slug, setSlug] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      onErased(await operatorApi.erase(tenant.id, slug))
    } catch (err) {
      setError(message(err, `Could not erase ${tenant.slug}`))
    }
  }

  return (
    <form className="identity-form" onSubmit={submit} aria-label="Erase tenant">
      <p>
        Erasing removes every row, file and secret of <strong>{tenant.display_name}</strong>, for good. A deletion
        certificate (counts per table) is kept in the platform log. {exportText(tenant)}.
      </p>
      <label>
        Type {tenant.slug} to confirm
        <input value={slug} onChange={(e) => setSlug(e.target.value)} autoComplete="off" />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="registry-entry-actions">
        <button type="submit" disabled={slug !== tenant.slug}>Erase {tenant.slug}</button>
      </div>
    </form>
  )
}

function Certificates({ certificates }: { certificates: DeletionCertificate[] }) {
  const [open, setOpen] = useState<string | null>(null)
  if (certificates.length === 0) return <p className="meta">No tenant has been erased.</p>
  return (
    <ul>
      {certificates.map((cert) => (
        <li key={cert.id}>
          {cert.certificate.slug} <span className="meta">({cert.tenant_id})</span>: erased {when(cert.occurred_at)} by{' '}
          {cert.operator_name} -- {cert.certificate.rows_total} rows, {cert.certificate.files_removed} files{' '}
          <button type="button" className="button-secondary" onClick={() => setOpen(open === cert.id ? null : cert.id)}>
            {open === cert.id ? 'Hide' : 'Certificate'}
          </button>
          {open === cert.id && <pre className="certificate">{JSON.stringify(cert.certificate, null, 2)}</pre>}
        </li>
      ))}
    </ul>
  )
}

function OtherRegions({ config }: { config: OperatorConfig }) {
  const others = Object.entries(config.regions).filter(([region]) => region !== config.region)
  if (others.length === 0) return null
  return (
    <>
      {' '}Other regions:{' '}
      {others.map(([region, url], i) => (
        <span key={region}>{i > 0 && ', '}<a href={url}>{region}</a></span>
      ))}
    </>
  )
}

function Console({ operator, config, onSignedOut }: { operator: Operator; config: OperatorConfig; onSignedOut: () => void }) {
  const [tenants, setTenants] = useState<Tenant[] | null>(null)
  const [events, setEvents] = useState<PlatformEvent[]>([])
  const [open, setOpen] = useState<string | null>(null)
  const [limitsOpen, setLimitsOpen] = useState<string | null>(null)
  const [invitation, setInvitation] = useState<TenantCreated | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [eraseOpen, setEraseOpen] = useState<string | null>(null)
  const [certificates, setCertificates] = useState<DeletionCertificate[]>([])
  const [erased, setErased] = useState<DeletionCertificate | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    operatorApi.tenants().then(setTenants).catch((err) => setError(message(err, 'Could not load tenants')))
    operatorApi.audit().then(setEvents).catch(() => setEvents([]))
    operatorApi.certificates().then(setCertificates).catch(() => setCertificates([]))
  }, [])
  useEffect(load, [load])

  async function change(tenant: Tenant, action: StatusAction) {
    setError(null)
    setConfirmDelete(null)
    try {
      await operatorApi.changeStatus(tenant.id, action)
      load()
    } catch (err) {
      setError(message(err, `Could not ${action} ${tenant.slug}`))
    }
  }

  async function reinvite(tenant: Tenant) {
    setError(null)
    const email = tenant.first_admin?.email ?? window.prompt(`The first admin's email for ${tenant.slug}`) ?? ''
    if (!email) return
    try {
      setInvitation(await operatorApi.invite(tenant.id, email))
      load()
    } catch (err) {
      setError(message(err, `Could not invite ${tenant.slug}'s first admin`))
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
        <p className="meta">Usage is counts and costs only. To look inside a tenant, its admin grants support access.</p>
        {config.region && (
          <p className="meta" aria-label="Deployment region">
            This deployment serves region <strong>{config.region}</strong>: its tenants' data and model calls stay there.
            <OtherRegions config={config} />
          </p>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        {erased && (
          <div className="support-banner" role="status" aria-label="Tenant erased">
            Erased {erased.certificate.slug}: {erased.certificate.rows_total} rows and {erased.certificate.files_removed}{' '}
            files. The deletion certificate is in the platform log.
          </div>
        )}
        {invitation && <InvitationLink created={invitation} />}
        {tenants && (
          <table className="data-table">
            <thead>
              <tr>
                <th>Tenant</th><th>Status</th><th>Hosts</th><th>Users</th><th>Processes</th><th>Jobs waiting</th>
                <th>First admin</th><th>LLM this month</th><th>Last sign-in</th><th>Support</th><th />
              </tr>
            </thead>
            <tbody>
              {tenants.map((tenant) => (
                <tr key={tenant.id}>
                  <td>
                    {tenant.display_name} <span className="meta">({tenant.slug}{tenant.region ? `, ${tenant.region}` : ''})</span>
                    {tenant.dedicated_database && <> <span className="badge">own database</span></>}
                  </td>
                  <td>
                    <span className="badge">{tenant.status}</span>
                    {tenant.status === 'suspended' && tenant.suspended_at && <span className="meta"> since {when(tenant.suspended_at)}</span>}
                    {tenant.status === 'deleting' && tenant.delete_after && (
                      <span className="meta"> {erasable(tenant) ? 'can be erased now' : `erasable from ${when(tenant.delete_after)}`}</span>
                    )}
                    {tenant.status !== 'active' && <div className="meta">{exportText(tenant)}</div>}
                  </td>
                  <td>{tenant.hosts.join(', ')}</td>
                  <td>{tenant.usage?.users ?? '--'}</td>
                  <td>{tenant.usage?.processes ?? '--'}</td>
                  <td>{tenant.usage?.jobs_waiting ?? '--'}</td>
                  <td>
                    {tenant.first_admin ? FIRST_ADMIN_TEXT[tenant.first_admin.state] : '--'}
                    {tenant.first_admin?.email && <span className="meta"> ({tenant.first_admin.email})</span>}
                    {tenant.status === 'active' && tenant.created_via === 'console' &&
                      (tenant.first_admin?.state === 'invited' || tenant.first_admin?.state === 'expired' || tenant.first_admin?.state === 'none') && (
                        <>
                          {' '}
                          <button type="button" className="button-secondary" onClick={() => reinvite(tenant)}>New invitation</button>
                        </>
                      )}
                  </td>
                  <td>
                    {tenant.usage ? usd(tenant.usage.llm_cost_this_month_usd) : '--'}
                    {tenant.effective_limits.llm_monthly_budget_usd !== null && (
                      <span className="meta"> of {usd(tenant.effective_limits.llm_monthly_budget_usd)}</span>
                    )}{' '}
                    <button type="button" className="button-secondary" onClick={() => setLimitsOpen(limitsOpen === tenant.id ? null : tenant.id)}>
                      Limits
                    </button>
                  </td>
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
                    {tenant.status === 'deleting' && (
                      <>
                        <button type="button" className="button-secondary" onClick={() => change(tenant, 'restore')}>Restore</button>{' '}
                        {erasable(tenant) && (
                          <button type="button" className="button-secondary" onClick={() => setEraseOpen(eraseOpen === tenant.id ? null : tenant.id)}>
                            Erase
                          </button>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {eraseOpen && tenants?.find((t) => t.id === eraseOpen) && (
          <section aria-label="Erase">
            <h2>Erase: {tenants.find((t) => t.id === eraseOpen)!.display_name}</h2>
            <Erase
              key={eraseOpen}
              tenant={tenants.find((t) => t.id === eraseOpen)!}
              onErased={(cert) => {
                setEraseOpen(null)
                setErased(cert)
                load()
              }}
            />
          </section>
        )}
        {limitsOpen && tenants?.find((t) => t.id === limitsOpen) && (
          <section aria-label="Limits">
            <h2>Limits: {tenants.find((t) => t.id === limitsOpen)!.display_name}</h2>
            <Limits
              key={limitsOpen}
              tenant={tenants.find((t) => t.id === limitsOpen)!}
              onSaved={() => {
                setLimitsOpen(null)
                load()
              }}
            />
          </section>
        )}
        {open && tenants?.find((t) => t.id === open) && (
          <section aria-label="Support access">
            <h2>Support access: {tenants.find((t) => t.id === open)!.display_name}</h2>
            <SupportAccess tenant={tenants.find((t) => t.id === open)!} />
          </section>
        )}
        <section>
          <h2>Create a tenant</h2>
          <CreateTenant
            onCreated={(created) => {
              setInvitation(created.invitation_url ? created : null)
              load()
            }}
          />
        </section>
        <section aria-label="Deletion certificates">
          <h2>Deletion certificates</h2>
          <Certificates certificates={certificates} />
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
  if (!config) return null
  if (!operator) return <SignIn config={config} onSignedIn={setOperator} />
  return <Console operator={operator} config={config} onSignedOut={() => setOperator(null)} />
}
