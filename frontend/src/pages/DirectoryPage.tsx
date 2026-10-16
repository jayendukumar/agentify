import { useEffect, useState, type FormEvent } from 'react'
import {
  ApiError,
  createScimToken,
  listContextEntities,
  listGroupMappings,
  listKnownGroups,
  previewGroupMappings,
  revokeScimToken,
  saveGroupMappings,
  getScimSetup,
  getIdentitySettings,
  saveIdentitySettings,
  testIdentitySettings,
  grantSupport,
  listSupportGrants,
  revokeSupport,
} from '../api/client'
import type {
  ConnectionTest,
  ContextEntity,
  GroupMapping,
  IdentitySettings,
  IdentitySettingsInput,
  KnownGroup,
  RoleChange,
  RoleGrantChange,
  ScimSetup,
  ScimTokenIssued,
  SupportGrant,
  SupportGrantInput,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { ROLE_INFO, roleLabel } from '../auth/roles'

function when(iso: string | null): string {
  if (!iso) return 'never'
  return new Date(iso.endsWith('Z') ? iso : `${iso}Z`).toLocaleString()
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

function toInput(s: IdentitySettings): IdentitySettingsInput {
  return {
    auth_mode: 'oidc', // the form sets up single sign-on; local sign-in is a development default only
    issuer: s.issuer,
    discovery_url: s.discovery_url,
    client_id: s.client_id,
    client_secret_ref: s.client_secret_ref || 'secret://tenant/oidc_client_secret',
    display_name: s.display_name,
    scopes: s.scopes,
    acr_values: s.acr_values,
    groups_claim: s.groups_claim,
    jit_provisioning: s.jit_provisioning,
    require_mfa: s.require_mfa,
    mfa_amr_values: s.mfa_amr_values,
    mfa_acr_values: s.mfa_acr_values,
    scim_link_claim: s.scim_link_claim,
  }
}

const list = (value: string) => value.split(',').map((v) => v.trim()).filter(Boolean)

const SOURCE_TEXT: Record<IdentitySettings['source'], string> = {
  tenant: 'Saved here',
  environment: "The deployment's defaults (environment variables) -- saving here replaces them for this organization",
  unset: 'Not set up yet',
}

// Epic 38, US38.6: which identity provider this organization signs in with.
// Saving needs a passing connection test of exactly what's being saved:
// settings that can't reach the IdP would lock everyone out.
function IdentitySection() {
  const [current, setCurrent] = useState<IdentitySettings | null>(null)
  const [form, setForm] = useState<IdentitySettingsInput | null>(null)
  const [tested, setTested] = useState<{ input: string; result: ConnectionTest } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    getIdentitySettings()
      .then((s) => {
        setCurrent(s)
        setForm(toInput(s))
      })
      .catch((err) => setError(errorText(err, 'Failed to load the sign-in settings')))
  }, [])

  if (!current || !form) return error ? <p className="error" role="alert">{error}</p> : null

  const key = JSON.stringify(form)
  const passed = tested?.input === key && tested.result.ok
  const update = (patch: Partial<IdentitySettingsInput>) => {
    setForm({ ...form, ...patch })
    setSaved(false)
  }

  async function test() {
    setBusy(true)
    setError(null)
    try {
      setTested({ input: key, result: await testIdentitySettings(form!) })
    } catch (err) {
      setError(errorText(err, 'Could not test the connection'))
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const next = await saveIdentitySettings(form!)
      setCurrent(next)
      setForm(toInput(next))
      setSaved(true)
    } catch (err) {
      setError(errorText(err, 'Could not save the sign-in settings'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h2>Sign-in (identity provider)</h2>
      <p className="meta">
        People in this organization sign in with your identity provider (Entra ID, Okta, Keycloak) over OpenID Connect,
        with multi-factor authentication. Only this organization uses these settings.
      </p>
      <dl className="directory-facts">
        <dt>Currently</dt>
        <dd>
          {current.auth_mode === 'oidc' ? `Single sign-on (${current.display_name})` : current.auth_mode === 'local' ? 'Local sign-in (not for production)' : 'Nobody can sign in yet'}
          {' -- '}
          {SOURCE_TEXT[current.source]}
          {current.updated_by && ` (by ${current.updated_by}, ${when(current.updated_at)})`}
        </dd>
        <dt>Redirect URI</dt>
        <dd><code>{current.redirect_uri}</code></dd>
        <dt>Sign-out redirect</dt>
        <dd><code>{current.post_logout_redirect_uri}</code></dd>
        <dt>Back-channel logout</dt>
        <dd><code>{current.backchannel_logout_url}</code></dd>
      </dl>
      <p className="meta">Register these in your identity provider's app registration for Axyntro.</p>
      <div className="identity-form">
        <label>Issuer<input value={form.issuer} onChange={(e) => update({ issuer: e.target.value })} placeholder="https://login.example.com/tenant-id/v2.0" /></label>
        <label>Discovery URL (optional)<input value={form.discovery_url} onChange={(e) => update({ discovery_url: e.target.value })} placeholder="defaults to <issuer>/.well-known/openid-configuration" /></label>
        <label>Client ID<input value={form.client_id} onChange={(e) => update({ client_id: e.target.value })} /></label>
        <label>
          Client secret reference
          <input value={form.client_secret_ref} onChange={(e) => update({ client_secret_ref: e.target.value })} />
          <span className="meta">
            Not the secret itself: a reference to your organization's own secret, like{' '}
            <code>secret://tenant/oidc_client_secret</code>. The hosting company stores it as{' '}
            <code>{current.secret_location}</code>.
            {current.secret_configured ? ' The current one is set.' : ''}
          </span>
        </label>
        <label>Button name<input value={form.display_name} onChange={(e) => update({ display_name: e.target.value })} /></label>
        <label>Ask the IdP for (acr_values)<input value={form.acr_values} onChange={(e) => update({ acr_values: e.target.value })} placeholder="e.g. mfa" /></label>
        <label>MFA when amr includes<input value={form.mfa_amr_values.join(', ')} onChange={(e) => update({ mfa_amr_values: list(e.target.value) })} /></label>
        <label>... or acr is<input value={form.mfa_acr_values.join(', ')} onChange={(e) => update({ mfa_acr_values: list(e.target.value) })} /></label>
        <label>Groups claim<input value={form.groups_claim} onChange={(e) => update({ groups_claim: e.target.value })} /></label>
        <label>SCIM link claim<input value={form.scim_link_claim} onChange={(e) => update({ scim_link_claim: e.target.value })} /></label>
        <label className="login-role-option">
          <input type="checkbox" checked={form.jit_provisioning} onChange={(e) => update({ jit_provisioning: e.target.checked })} />
          <span>Create people at their first sign-in (otherwise only people provisioned over SCIM)</span>
        </label>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {tested && tested.input === key && (
        <div role="status" className={tested.result.ok ? 'directory-test-ok' : 'error'}>
          {tested.result.ok
            ? `Connected: ${tested.result.issuer}, ${tested.result.signing_keys} signing key(s).`
            : `The connection test failed: ${tested.result.problems.join('; ')}`}
        </div>
      )}
      {saved && <p role="status">Saved. The next sign-in uses these settings.</p>}
      <div className="registry-entry-actions">
        <button type="button" className="button-secondary" disabled={busy} onClick={test}>Test connection</button>{' '}
        <button type="button" disabled={busy || !passed} onClick={save} title={passed ? undefined : 'Test these settings first'}>
          Save sign-in settings
        </button>
      </div>
    </section>
  )
}

const NEW_GRANT: SupportGrantInput = { access: 'read', hours: 24, operator_email: null, reason: '' }

// Epic 38, US38.8: let the hosting company's support in, for a set time.
// Read-only unless the admin says otherwise; never admin or approver.
function SupportSection() {
  const [grants, setGrants] = useState<SupportGrant[] | null>(null)
  const [form, setForm] = useState<SupportGrantInput>(NEW_GRANT)
  const [error, setError] = useState<string | null>(null)

  const load = () =>
    listSupportGrants().then(setGrants).catch((err) => setError(errorText(err, 'Failed to load support access')))
  useEffect(() => {
    load()
  }, [])

  async function grant(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await grantSupport({ ...form, operator_email: form.operator_email?.trim() || null })
      setForm(NEW_GRANT)
      await load()
    } catch (err) {
      setError(errorText(err, 'Could not grant support access'))
    }
  }

  async function revoke(id: string) {
    setError(null)
    try {
      await revokeSupport(id)
      await load()
    } catch (err) {
      setError(errorText(err, 'Could not revoke support access'))
    }
  }

  const active = grants?.filter((g) => g.active) ?? []
  return (
    <section>
      <h2>Support access</h2>
      <p className="meta">
        The hosting company's support can't see your data unless you let them in here, for a set time. Read-only support
        can look but change nothing; support that can edit works on processes and agents, but never manages users,
        sign-in or approvals. Everything they do is in your audit log.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {active.length === 0 ? (
        <p className="meta">No support access right now.</p>
      ) : (
        <ul>
          {active.map((g) => (
            <li key={g.id}>
              {g.access === 'read' ? 'Read-only' : 'Can edit'} for {g.operator_email ?? 'any operator'}, until{' '}
              {when(g.expires_at)} (granted by {g.granted_by}: <em>{g.reason}</em>)
              {g.live_sessions > 0 && ` -- ${g.live_sessions} support session(s) open`}{' '}
              <button type="button" className="button-secondary" onClick={() => revoke(g.id)}>Revoke</button>
            </li>
          ))}
        </ul>
      )}
      <form className="identity-form" onSubmit={grant} aria-label="Grant support access">
        <label>
          Reason
          <input value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Ticket 4711: batch results look wrong" required minLength={3} />
        </label>
        <label>
          Access
          <select value={form.access} onChange={(e) => setForm({ ...form, access: e.target.value as 'read' | 'write' })}>
            <option value="read">Read-only</option>
            <option value="write">Can edit processes and agents</option>
          </select>
        </label>
        <label>
          For (hours)
          <input type="number" min={1} value={form.hours} onChange={(e) => setForm({ ...form, hours: Number(e.target.value) })} />
        </label>
        <label>
          Only this operator (email, optional)
          <input type="email" value={form.operator_email ?? ''} onChange={(e) => setForm({ ...form, operator_email: e.target.value })} />
        </label>
        <div className="registry-entry-actions"><button type="submit">Grant support access</button></div>
      </form>
    </section>
  )
}

// Epic 37, US37.10: the IdP's provisioning connection. A token is shown
// once, when it's generated; afterwards only its label and use are known.
function ScimSection() {
  const [setup, setSetup] = useState<ScimSetup | null>(null)
  const [label, setLabel] = useState('')
  const [issued, setIssued] = useState<ScimTokenIssued | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getScimSetup().then(setSetup).catch((err) => setError(errorText(err, 'Failed to load SCIM settings')))
  }, [])

  async function generate() {
    setError(null)
    try {
      const token = await createScimToken(label.trim() || 'Identity provider')
      setIssued(token)
      setLabel('')
      setSetup(await getScimSetup())
    } catch (err) {
      setError(errorText(err, 'Could not generate a token'))
    }
  }

  async function revoke(tokenId: string) {
    setError(null)
    try {
      await revokeScimToken(tokenId)
      setSetup(await getScimSetup())
    } catch (err) {
      setError(errorText(err, 'Could not revoke the token'))
    }
  }

  return (
    <section>
      <h2>Provisioning (SCIM)</h2>
      <p className="meta">
        Your identity provider (Entra ID, Okta) creates, updates and deactivates people here, and keeps their groups current.
        Deactivating someone in the directory ends every session they have at once.
      </p>
      {setup && (
        <dl className="directory-facts">
          <dt>SCIM base URL</dt>
          <dd><code>{setup.base_url}</code></dd>
          <dt>Links to sign-in by</dt>
          <dd>
            the <code>{setup.link_claim}</code> claim, which must equal the user's SCIM <code>externalId</code>
          </dd>
        </dl>
      )}
      <p className="meta">
        Not to be confused with the SCIM <em>connector</em> on the Organization page: that one reads groups into
        organization context (who has approval authority). This one controls who can use Axyntro. One group can feed both.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      {issued && (
        <div className="directory-token" role="status">
          <p>
            <strong>Copy this token now.</strong> It won't be shown again. Paste it into your identity provider's provisioning
            settings as the secret token.
          </p>
          <code className="directory-token-value">{issued.token}</code>{' '}
          <button type="button" className="button-secondary" onClick={() => navigator.clipboard?.writeText(issued.token)}>
            Copy
          </button>{' '}
          <button type="button" className="button-secondary" onClick={() => setIssued(null)}>Done</button>
        </div>
      )}
      <div className="registry-entry-actions">
        <label>
          Token label{' '}
          <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Entra ID production" maxLength={80} />
        </label>
        <button type="button" onClick={generate}>Generate token</button>
      </div>
      {setup && setup.tokens.length > 0 && (
        <table className="org-register">
          <thead>
            <tr><th>Label</th><th>Created</th><th>Last used</th><th>Status</th></tr>
          </thead>
          <tbody>
            {setup.tokens.map((t) => (
              <tr key={t.id}>
                <td>{t.label}</td>
                <td>{when(t.created_at)}</td>
                <td>{when(t.last_used_at)}</td>
                <td>
                  {t.revoked_at ? `Revoked ${when(t.revoked_at)}` : (
                    <>
                      Active{' '}
                      <button type="button" className="button-secondary" onClick={() => revoke(t.id)}>Revoke</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

function grantText(g: RoleGrantChange): string {
  return g.scope_type === 'business_unit' ? `${roleLabel(g.role)} (${g.scope_name ?? g.scope_id})` : roleLabel(g.role)
}

const BLANK: GroupMapping = { group: '', role: 'analyst', scope_type: 'organization', scope_id: '' }

// Epic 37, US37.11: which directory groups grant which roles. Edits are
// previewed -- who gains or loses what -- before they're saved.
function MappingsSection() {
  const [saved, setSaved] = useState<GroupMapping[]>([])
  const [draft, setDraft] = useState<GroupMapping[]>([])
  const [groups, setGroups] = useState<KnownGroup[]>([])
  const [units, setUnits] = useState<ContextEntity[]>([])
  const [preview, setPreview] = useState<RoleChange[] | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([listGroupMappings(), listKnownGroups(), listContextEntities('business_unit')])
      .then(([mappings, known, unitList]) => {
        setSaved(mappings)
        setDraft(mappings)
        setGroups(known)
        setUnits(unitList)
      })
      .catch((err) => setError(errorText(err, 'Failed to load group mappings')))
  }, [])

  const dirty = JSON.stringify(draft) !== JSON.stringify(saved)

  function update(index: number, change: Partial<GroupMapping>) {
    setPreview(null)
    setMessage(null)
    setDraft((rows) => rows.map((row, i) => (i === index ? { ...row, ...change } : row)))
  }

  async function runPreview() {
    setError(null)
    try {
      setPreview(await previewGroupMappings(draft))
    } catch (err) {
      setError(errorText(err, 'Could not preview the changes'))
    }
  }

  async function save() {
    setError(null)
    try {
      const result = await saveGroupMappings(draft)
      setSaved(result.mappings)
      setDraft(result.mappings)
      setPreview(null)
      setMessage(`Saved. ${result.changes.length} ${result.changes.length === 1 ? "person's" : "people's"} roles changed.`)
    } catch (err) {
      setError(errorText(err, 'Could not save the mappings'))
    }
  }

  return (
    <section>
      <h2>Group mappings</h2>
      <p className="meta">
        Members of a group get its roles, organization-wide or for one business unit. Roles from groups change in the directory,
        not on the Users page. Name a group as your identity provider sends it: its name, or for Entra ID its object id.
        A change applies at once to everyone provisioned over SCIM or seen at sign-in. Anyone whose groups aren't known yet
        is updated when they next sign in.
      </p>
      <datalist id="known-groups">
        {groups.map((g) => (
          <option key={`${g.source}:${g.name}`} value={g.name}>
            {g.source === 'scim' ? `SCIM, ${g.members} members` : `seen at sign-in, ${g.members} people`}
          </option>
        ))}
      </datalist>
      {error && <p className="error" role="alert">{error}</p>}
      <table className="org-register">
        <thead>
          <tr><th>Group</th><th>Role</th><th>Scope</th><th aria-label="Actions" /></tr>
        </thead>
        <tbody>
          {draft.map((row, index) => (
            <tr key={index}>
              <td>
                <input
                  list="known-groups"
                  value={row.group}
                  aria-label={`Group for mapping ${index + 1}`}
                  onChange={(e) => update(index, { group: e.target.value })}
                />
              </td>
              <td>
                <select value={row.role} aria-label={`Role for mapping ${index + 1}`} onChange={(e) => update(index, { role: e.target.value as GroupMapping['role'] })}>
                  {ROLE_INFO.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                </select>
              </td>
              <td>
                <select
                  value={row.scope_id}
                  aria-label={`Scope for mapping ${index + 1}`}
                  onChange={(e) => update(index, {
                    scope_id: e.target.value, scope_type: e.target.value ? 'business_unit' : 'organization',
                  })}
                >
                  <option value="">Whole organization</option>
                  {units.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
              </td>
              <td>
                <button type="button" className="button-secondary" onClick={() => { setPreview(null); setDraft((rows) => rows.filter((_, i) => i !== index)) }}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="registry-entry-actions">
        <button type="button" className="button-secondary" onClick={() => { setPreview(null); setDraft((rows) => [...rows, BLANK]) }}>
          Add mapping
        </button>
        <button type="button" className="button-secondary" disabled={!dirty} onClick={runPreview}>Preview changes</button>
        <button type="button" disabled={!dirty || preview === null} onClick={save} title={preview === null ? 'Preview first' : undefined}>
          Save mappings
        </button>
        {dirty && <button type="button" className="button-secondary" onClick={() => { setDraft(saved); setPreview(null) }}>Discard</button>}
        {message && <span className="meta" role="status">{message}</span>}
      </div>
      {preview !== null && (
        <div role="region" aria-label="Preview of role changes">
          {preview.length === 0 ? (
            <p className="meta">Nobody's roles would change.</p>
          ) : (
            <table className="org-register">
              <thead>
                <tr><th>Person</th><th>Would gain</th><th>Would lose</th></tr>
              </thead>
              <tbody>
                {preview.map((c) => (
                  <tr key={c.user_id}>
                    <td>{c.user_name}</td>
                    <td>{c.gained.map(grantText).join(', ') || '-'}</td>
                    <td>{c.lost.map(grantText).join(', ') || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  )
}

export default function DirectoryPage() {
  const { can } = useAuth()
  if (!can('users.manage')) {
    return (
      <div className="page">
        <h1>Directory</h1>
        <p className="meta">Managing the directory connection needs the Admin role.</p>
      </div>
    )
  }
  return (
    <div className="page">
      <h1>Directory</h1>
      <IdentitySection />
      <SupportSection />
      <ScimSection />
      <MappingsSection />
    </div>
  )
}
