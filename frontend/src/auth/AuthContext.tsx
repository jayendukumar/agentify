import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { getCurrentUser, login as apiLogin, logout as apiLogout } from '../api/client'
import type { Role, User } from '../api/types'

interface AuthContextValue {
  user: User | null
  loading: boolean
  login: (name: string, role: Role) => Promise<void>
  logout: () => Promise<void>
}

// Permissive editor-shaped default so a page calling useAuth() outside a
// wrapping <AuthProvider> (every existing page test, which doesn't test
// auth itself) still renders as if logged in as an editor, rather than
// crashing or silently disabling every button. The real app always
// renders inside AuthProvider (see App.tsx), where this default is
// immediately replaced by the real /api/auth/me result.
const defaultUser: User = { id: 'stub', name: 'Editor', role: 'editor', created_at: '' }

const AuthContext = createContext<AuthContextValue>({
  user: defaultUser,
  loading: false,
  login: async () => {},
  logout: async () => {},
})

export function useAuth(): AuthContextValue {
  return useContext(AuthContext)
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getCurrentUser()
      .then(setUser)
      .finally(() => setLoading(false))
  }, [])

  async function login(name: string, role: Role) {
    setUser(await apiLogin(name, role))
  }

  async function logout() {
    await apiLogout()
    setUser(null)
  }

  return <AuthContext.Provider value={{ user, loading, login, logout }}>{children}</AuthContext.Provider>
}
