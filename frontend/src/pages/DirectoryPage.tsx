import { useEffect, useState } from 'react'
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
} from '../api/client'
import type { ContextEntity, GroupMapping, KnownGroup, RoleChange, RoleGrantChange, ScimSetup, ScimTokenIssued } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { ROLE_INFO, roleLabel } from '../auth/roles'

function when(iso: string | null): string {
  if (!iso) return 'never'
  return new Date(iso.endsWith('Z') ? iso : `${iso}Z`).toLocaleString()
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
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
      <ScimSection />
      <MappingsSection />
    </div>
  )
}
