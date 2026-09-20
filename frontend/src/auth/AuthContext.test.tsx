import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { User } from '../api/types'
import { AuthProvider, useAuth } from './AuthContext'

const getCurrentUser = vi.fn()
const login = vi.fn()
const logout = vi.fn()
let unauthorizedListener: (() => void) | null = null

vi.mock('../api/client', () => ({
  getCurrentUser: (...args: unknown[]) => getCurrentUser(...args),
  login: (...args: unknown[]) => login(...args),
  logout: (...args: unknown[]) => logout(...args),
  onUnauthorized: (listener: () => void) => {
    unauthorizedListener = listener
  },
}))

const editor: User = { id: 'user-1', name: 'Alice', role: 'editor', created_at: '2026-01-01T00:00:00Z' }

function Probe() {
  const { user, loading } = useAuth()
  if (loading) return <p>loading</p>
  return <p>{user ? `logged in as ${user.name} (${user.role})` : 'logged out'}</p>
}

describe('AuthContext', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows logged-out state when there is no session', async () => {
    getCurrentUser.mockResolvedValue(null)
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    expect(await screen.findByText('logged out')).toBeInTheDocument()
  })

  it('shows the logged-in user once /api/auth/me resolves', async () => {
    getCurrentUser.mockResolvedValue(editor)
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    expect(await screen.findByText('logged in as Alice (editor)')).toBeInTheDocument()
  })

  it('login() updates the current user', async () => {
    getCurrentUser.mockResolvedValue(null)
    login.mockResolvedValue(editor)

    function LoginButton() {
      const { login: doLogin } = useAuth()
      return (
        <button type="button" onClick={() => void doLogin('Alice', 'editor')}>
          Log in
        </button>
      )
    }

    const user = userEvent.setup()
    render(
      <AuthProvider>
        <Probe />
        <LoginButton />
      </AuthProvider>,
    )

    await screen.findByText('logged out')
    await user.click(screen.getByRole('button', { name: /log in/i }))

    expect(await screen.findByText('logged in as Alice (editor)')).toBeInTheDocument()
    expect(login).toHaveBeenCalledWith('Alice', 'editor')
  })

  it('logout() clears the current user', async () => {
    getCurrentUser.mockResolvedValue(editor)
    logout.mockResolvedValue(undefined)

    function LogoutButton() {
      const { logout: doLogout } = useAuth()
      return (
        <button type="button" onClick={() => void doLogout()}>
          Log out
        </button>
      )
    }

    const user = userEvent.setup()
    render(
      <AuthProvider>
        <Probe />
        <LogoutButton />
      </AuthProvider>,
    )

    await screen.findByText('logged in as Alice (editor)')
    await user.click(screen.getByRole('button', { name: /log out/i }))

    await waitFor(() => expect(screen.getByText('logged out')).toBeInTheDocument())
  })

  it('drops back to logged-out when any API call reports the session expired', async () => {
    getCurrentUser.mockResolvedValue(editor)
    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    )

    await screen.findByText('logged in as Alice (editor)')

    // Simulates client.ts's request() calling this after any 401 response,
    // e.g. a diagram/blueprint/chat call made after the session's expiry.
    act(() => unauthorizedListener?.())

    await waitFor(() => expect(screen.getByText('logged out')).toBeInTheDocument())
  })
})
