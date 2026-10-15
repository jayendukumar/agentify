import { Fragment, useEffect, useState } from 'react'
import {
  ApiError,
  getGovernanceSettings,
  listContextEntities,
  listUserSessions,
  listUsers,
  revokeAllUserSessions,
  revokeUserSession,
  setGovernanceSettings,
  setUserActive,
  setUserRoles,
} from '../api/client'
import type { ContextEntity, GovernanceSettings, Role, SessionInfo, User } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { grantLabel, ROLE_INFO } from '../auth/roles'
import SessionList from '../components/SessionList'

// Roles from IdP groups (Epic 37, US37.7) change in the directory, not
// here: they're listed but not editable, and saving leaves them alone.
function RoleEditor({ user, units, onSaved }: { user: User; units: ContextEntity[]; onSaved: (u: User) => void }) {
  const editable = user.roles.filter((g) => g.source !== 'group')
  const fromGroups = user.roles.filter((g) => g.source === 'group')
  const [roles, setRoles] = useState<Role[]>(() => [...new Set(editable.map((g) => g.role))])
  const [scope, setScope] = useState(() => editable.find((g) => g.scope_type === 'business_unit')?.scope_id ?? '')
  const [message, setMessage] = useState<string | null>(null)

  async function save() {
    setMessage(null)
    try {
      const grants = roles.map((role) => ({
        role,
        scope_type: (scope ? 'business_unit' : 'organization') as 'business_unit' | 'organization',
        scope_id: scope,
      }))
      onSaved(await setUserRoles(user.id, grants))
      setMessage('Saved.')
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not save roles')
    }
  }

  return (
    <div className="registry-entry-actions">
      {fromGroups.length > 0 && (
        <span className="meta">From directory groups: {fromGroups.map(grantLabel).join(', ')}.</span>
      )}
      {ROLE_INFO.map((info) => (
        <label key={info.value} className="check-row" title={info.description}>
          <input
            type="checkbox"
            checked={roles.includes(info.value)}
            onChange={(e) =>
              setRoles((current) => (e.target.checked ? [...current, info.value] : current.filter((r) => r !== info.value)))
            }
          />
          {info.label}
        </label>
      ))}
      <label>
        Scope{' '}
        <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label={`Scope for ${user.name}`}>
          <option value="">Whole organization</option>
          {units.map((unit) => (
            <option key={unit.id} value={unit.id}>{unit.name}</option>
          ))}
        </select>
      </label>
      <button type="button" className="button-secondary" onClick={save}>Save roles</button>
      {message && <span className="meta" role="status">{message}</span>}
    </div>
  )
}

// Epic 37, US37.13: an admin ends a lost laptop's session, or all of a
// user's sessions, without waiting for the IdP. Takes effect on the
// browser's next request.
function UserSessions({ user, onCountChange }: { user: User; onCountChange: (count: number) => void }) {
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    listUserSessions(user.id)
      .then(setSessions)
      .catch((err) => setMessage(err instanceof ApiError ? err.message : 'Failed to load sessions'))
  }, [user.id])

  function update(next: SessionInfo[]) {
    setSessions(next)
    onCountChange(next.length)
  }

  async function revoke(session: SessionInfo) {
    setMessage(null)
    try {
      await revokeUserSession(user.id, session.id)
      update((sessions ?? []).filter((s) => s.id !== session.id))
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not revoke that session')
    }
  }

  async function revokeAll() {
    setMessage(null)
    try {
      const { revoked } = await revokeAllUserSessions(user.id)
      update([])
      setMessage(`Signed out of ${revoked} session${revoked === 1 ? '' : 's'}.`)
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not sign this user out')
    }
  }

  return (
    <div>
      {sessions === null ? <p className="meta" role="status">Loading sessions...</p> : <SessionList sessions={sessions} onEnd={revoke} />}
      {sessions && sessions.some((s) => !s.current) && (
        <button type="button" className="button-secondary" onClick={revokeAll}>
          Sign out everywhere
        </button>
      )}
      {message && <span className="meta" role="status"> {message}</span>}
    </div>
  )
}

function ApprovalPolicy({ units }: { units: ContextEntity[] }) {
  const [settings, setSettings] = useState<GovernanceSettings | null>(null)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    getGovernanceSettings().then(setSettings).catch(() => setSettings(null))
  }, [])

  if (!settings) return null

  async function save() {
    if (!settings) return
    setMessage(null)
    try {
      setSettings(await setGovernanceSettings(settings))
      setMessage('Saved.')
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : 'Could not save the approval policy')
    }
  }

  return (
    <section>
      <h2>Approval policy</h2>
      <p className="meta">
        How many independent approvers an agent release needs. Nobody can approve a version they built or edited.
      </p>
      <label>
        Organization default{' '}
        <input
          type="number"
          min={1}
          max={5}
          value={settings.required_agent_approvals}
          onChange={(e) => setSettings({ ...settings, required_agent_approvals: Number(e.target.value) || 1 })}
        />
      </label>
      {units.map((unit) => (
        <label key={unit.id}>
          {' '}{unit.name}{' '}
          <input
            type="number"
            min={1}
            max={5}
            placeholder="default"
            value={settings.required_agent_approvals_by_unit[unit.id] ?? ''}
            onChange={(e) => {
              const byUnit = { ...settings.required_agent_approvals_by_unit }
              if (e.target.value) byUnit[unit.id] = Number(e.target.value)
              else delete byUnit[unit.id]
              setSettings({ ...settings, required_agent_approvals_by_unit: byUnit })
            }}
          />
        </label>
      ))}
      <button type="button" className="button-secondary" onClick={save}>Save policy</button>
      {message && <span className="meta" role="status"> {message}</span>}
    </section>
  )
}

// Epic 37, US37.1: an admin grants roles (organization-wide or for one
// business unit) and deactivates leavers. Admin is deliberately a
// separate duty: it grants no build or approve permission.
export default function UsersPage() {
  const { user: me, can } = useAuth()
  const allowed = can('users.manage')
  const [users, setUsers] = useState<User[]>([])
  const [units, setUnits] = useState<ContextEntity[]>([])
  const [error, setError] = useState<string | null>(null)
  const [openSessions, setOpenSessions] = useState<string | null>(null)

  useEffect(() => {
    if (!allowed) return
    Promise.all([listUsers(), listContextEntities('business_unit')])
      .then(([userList, unitList]) => {
        setUsers(userList)
        setUnits(unitList)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load users'))
  }, [allowed])

  if (!allowed) {
    return (
      <div className="page">
        <h1>Users</h1>
        <p className="meta">Managing users needs the Admin role.</p>
      </div>
    )
  }

  function replace(updated: User) {
    setUsers((current) => current.map((u) => (u.id === updated.id ? updated : u)))
  }

  async function toggleActive(target: User) {
    setError(null)
    try {
      const updated = await setUserActive(target.id, !target.active)
      replace({ ...updated, active_sessions: updated.active ? target.active_sessions : 0 })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not change this account')
    }
  }

  return (
    <div className="page">
      <h1>Users</h1>
      <p className="meta">Roles decide what each person may do. A role scoped to a business unit covers only that unit's processes.</p>
      {error && <p className="error" role="alert">{error}</p>}
      <table className="org-register">
        <thead>
          <tr>
            <th>Name</th>
            <th>Current roles</th>
            <th>Change roles</th>
            <th>Status</th>
            <th>Sessions</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <Fragment key={u.id}>
              <tr>
                <td>{u.name}</td>
                <td>{u.roles.length ? u.roles.map(grantLabel).join(', ') : <span className="meta">Read only</span>}</td>
                <td>
                  <RoleEditor key={u.roles.map((g) => `${g.role}:${g.scope_id}`).join('|')} user={u} units={units} onSaved={replace} />
                </td>
                <td>
                  {u.active ? 'Active' : 'Deactivated'}{' '}
                  {u.id !== me?.id && (
                    <button type="button" className="button-secondary" onClick={() => toggleActive(u)}>
                      {u.active ? 'Deactivate' : 'Reactivate'}
                    </button>
                  )}
                </td>
                <td>
                  {u.active_sessions ?? 0}{' '}
                  <button
                    type="button"
                    className="button-secondary"
                    aria-expanded={openSessions === u.id}
                    onClick={() => setOpenSessions((current) => (current === u.id ? null : u.id))}
                  >
                    {openSessions === u.id ? 'Hide' : 'Show'}
                  </button>
                </td>
              </tr>
              {openSessions === u.id && (
                <tr>
                  <td colSpan={5}>
                    <UserSessions user={u} onCountChange={(count) => replace({ ...u, active_sessions: count })} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {can('org.admin') && <ApprovalPolicy units={units} />}
    </div>
  )
}
