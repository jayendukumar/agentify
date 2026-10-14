import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { getCurrentUser, login as apiLogin, logout as apiLogout, onUnauthorized } from '../api/client'
import type { Permission, Role, User } from '../api/types'

interface AuthContextValue {
  user: User | null
  loading: boolean
  login: (name: string, roles: Role[], businessUnitId?: string | null) => Promise<void>
  logout: () => Promise<void>
  // Epic 37, US37.2: whether to show an action. Permissions held at *some*
  // scope -- the server still checks the process's business unit.
  can: (permission: Permission) => boolean
}

const ALL_PERMISSIONS: Permission[] = [
  'process.edit', 'process.finalize', 'process.approve', 'process.delete', 'blueprint.edit',
  'agent.build', 'agent.test', 'agent.release_candidate', 'agent.approve', 'agent.release_criteria',
  'agent.publish', 'context.edit', 'context.confirm', 'catalog.edit', 'org.admin', 'users.manage', 'audit.read',
]

// Permissive default so a page calling useAuth() outside a wrapping
// <AuthProvider> (every existing page test, which doesn't test auth
// itself) still renders as if signed in with every permission, rather
// than crashing or silently disabling every button. The real app always
// renders inside AuthProvider (see App.tsx), where this default is
// immediately replaced by the real /api/auth/me result.
const defaultUser: User = { id: 'stub', name: 'Editor', active: true, roles: [], permissions: ALL_PERMISSIONS, created_at: '' }

function userCan(user: User | null, permission: Permission): boolean {
  return user?.permissions.includes(permission) ?? false
}

const AuthContext = createContext<AuthContextValue>({
  user: defaultUser,
  loading: false,
  login: async () => {},
  logout: async () => {},
  can: () => true,
})

export function useAuth(): AuthContextValue {
  return useContext(AuthContext)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Any API call can 401 once the session expires server-side, not just
    // this initial check -- client.ts calls this to drop the user straight
    // back to the login screen (App.tsx renders <LoginPage /> whenever
    // `user` is null) instead of leaving a stale, silently-failing page up.
    onUnauthorized(() => setUser(null))
    getCurrentUser()
      .then(setUser)
      .finally(() => setLoading(false))
  }, [])

  async function login(name: string, roles: Role[], businessUnitId?: string | null) {
    setUser(await apiLogin(name, roles, businessUnitId))
  }

  async function logout() {
    await apiLogout()
    setUser(null)
  }

  const can = (permission: Permission) => userCan(user, permission)

  return <AuthContext.Provider value={{ user, loading, login, logout, can }}>{children}</AuthContext.Provider>
}
