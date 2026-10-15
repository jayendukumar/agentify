import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import LoginPage from './LoginPage'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('Login introduction', () => {
  it('keeps the form hidden for ten seconds, then focuses the name field', () => {
    vi.useFakeTimers()
    render(<LoginPage />)
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
    act(() => vi.advanceTimersByTime(9999))
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1))
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
  })

  it('uses a static approved logo for reduced motion and allows skipping', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    render(<LoginPage />)
    expect(screen.getByRole('img', { name: 'Axyntro' })).toHaveAttribute('src', '/brand/axyntro-horizontal-violet-spark.png')
    fireEvent.click(screen.getByRole('button', { name: 'Skip introduction' }))
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
    // Epic 37: roles are picked as checkboxes, each with what it allows.
    fireEvent.click(screen.getByRole('checkbox', { name: /process analyst/i }))
    expect(screen.getByRole('checkbox', { name: /process analyst/i })).toBeChecked()
    expect(screen.getByText(/upload documents, edit diagrams/i)).toBeInTheDocument()
    expect(screen.getByText(/not for production/i)).toBeInTheDocument()
  })
})

describe('Single sign-on (Epic 37, US37.7)', () => {
  function stubConfig() {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      auth_mode: 'oidc', environment: 'production', roles: [], sso_name: 'Meridian SSO', sso_login_url: '/api/auth/oidc/login',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
  }

  it('offers the IdP instead of the name and role picker', async () => {
    stubConfig()
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    render(<LoginPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Skip introduction' }))
    const link = await screen.findByRole('link', { name: 'Sign in with Meridian SSO' })
    expect(link.getAttribute('href')).toMatch(/\/api\/auth\/oidc\/login\?return_to=%2F/)
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
    expect(screen.queryByText(/not for production/i)).not.toBeInTheDocument()
  })

  it('explains a refused sign-in and drops the code from the address bar', async () => {
    stubConfig()
    window.history.replaceState(null, '', '/?auth_error=mfa_required')
    render(<LoginPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent(/multi-factor authentication/i)
    expect(window.location.search).toBe('')
  })
})

describe('An organization without sign-in yet (Epic 38, US38.6)', () => {
  it('says so instead of offering a sign-in that cannot work', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      auth_mode: 'none', environment: 'production', roles: [], sso_name: null, sso_login_url: null,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    render(<LoginPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Skip introduction' }))
    expect(await screen.findByText(/Sign-in isn't set up for this organization yet/)).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Sign in with/ })).not.toBeInTheDocument()
  })
})
