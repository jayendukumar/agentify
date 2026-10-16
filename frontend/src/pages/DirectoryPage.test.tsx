import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DirectoryPage from './DirectoryPage'

const api = vi.hoisted(() => ({
  getScimSetup: vi.fn(),
  createScimToken: vi.fn(),
  revokeScimToken: vi.fn(),
  listGroupMappings: vi.fn(),
  listKnownGroups: vi.fn(),
  listContextEntities: vi.fn(),
  previewGroupMappings: vi.fn(),
  saveGroupMappings: vi.fn(),
  getIdentitySettings: vi.fn(),
  testIdentitySettings: vi.fn(),
  saveIdentitySettings: vi.fn(),
  listSupportGrants: vi.fn(),
  grantSupport: vi.fn(),
  revokeSupport: vi.fn(),
}))

vi.mock('../api/client', async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }))

const setup = { base_url: 'http://localhost:8000/scim/v2', link_claim: 'sub', tokens: [] }

const identity = {
  auth_mode: 'local', source: 'unset', issuer: '', discovery_url: '', client_id: '', client_secret_ref: '',
  display_name: 'single sign-on', scopes: 'openid profile email', acr_values: '', groups_claim: 'groups',
  jit_provisioning: true, require_mfa: true, mfa_amr_values: ['mfa', 'otp'], mfa_acr_values: [], scim_link_claim: 'sub',
  secret_configured: false, secret_location: 'TENANT_NORTHWIND_OIDC_CLIENT_SECRET (environment variable)',
  redirect_uri: 'http://northwind.localhost:8000/api/auth/oidc/callback',
  post_logout_redirect_uri: 'http://northwind.localhost:3000/',
  backchannel_logout_url: 'http://northwind.localhost:8000/api/auth/oidc/backchannel-logout',
  scim_base_url: 'http://northwind.localhost:8000/scim/v2', updated_at: null, updated_by: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getScimSetup.mockResolvedValue(setup)
  api.listGroupMappings.mockResolvedValue([{ group: 'axyntro-approvers', role: 'approver', scope_type: 'organization', scope_id: '' }])
  api.listKnownGroups.mockResolvedValue([{ name: 'axyntro-approvers', external_id: null, source: 'scim', members: 2 }])
  api.listContextEntities.mockResolvedValue([])
  api.getIdentitySettings.mockResolvedValue(identity)
  api.listSupportGrants.mockResolvedValue([])
})

describe('Directory page (Epic 37, US37.10-37.11)', () => {
  it('shows a new SCIM token once, with the base URL to configure', async () => {
    api.createScimToken.mockResolvedValue({ id: 't1', label: 'Entra ID', created_at: '2026-10-15T10:00:00', created_by: 'u',
      last_used_at: null, revoked_at: null, token: 'scim_secret_value' })
    render(<DirectoryPage />)
    expect(await screen.findByText('http://localhost:8000/scim/v2')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Token label'), { target: { value: 'Entra ID' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generate token' }))
    expect(await screen.findByText('scim_secret_value')).toBeInTheDocument()
    expect(api.createScimToken).toHaveBeenCalledWith('Entra ID')
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByText('scim_secret_value')).not.toBeInTheDocument()
  })

  it('previews who gains and loses roles before a mapping change can be saved', async () => {
    api.previewGroupMappings.mockResolvedValue([{
      user_id: 'u1', user_name: 'Avery Lindqvist',
      gained: [{ role: 'auditor', scope_type: 'organization', scope_id: '', scope_name: null }],
      lost: [{ role: 'approver', scope_type: 'organization', scope_id: '', scope_name: null }],
    }])
    api.saveGroupMappings.mockResolvedValue({ mappings: [], changes: [{}] })
    render(<DirectoryPage />)
    const role = await screen.findByLabelText('Role for mapping 1')
    const save = screen.getByRole('button', { name: 'Save mappings' })
    fireEvent.change(role, { target: { value: 'auditor' } })
    expect(save).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Preview changes' }))
    const preview = await screen.findByRole('region', { name: 'Preview of role changes' })
    expect(preview).toHaveTextContent('Avery Lindqvist')
    expect(preview).toHaveTextContent('Auditor')
    expect(preview).toHaveTextContent('Approver')
    expect(save).toBeEnabled()

    fireEvent.click(save)
    await waitFor(() => expect(api.saveGroupMappings).toHaveBeenCalledWith([
      { group: 'axyntro-approvers', role: 'auditor', scope_type: 'organization', scope_id: '' },
    ]))
  })
})

describe('Sign-in settings (Epic 38, US38.6)', () => {
  it('shows what to register at the IdP and saves only settings that just passed a connection test', async () => {
    api.testIdentitySettings.mockResolvedValue({ ok: true, issuer: 'http://localhost:8180/realms/northwind', signing_keys: 1,
      authorization_endpoint: 'a', token_endpoint: 't', end_session_endpoint: null, secret_configured: true, problems: [] })
    api.saveIdentitySettings.mockResolvedValue({ ...identity, auth_mode: 'oidc', source: 'tenant', updated_by: 'Nora' })
    render(<DirectoryPage />)
    expect(await screen.findByText('http://northwind.localhost:8000/api/auth/oidc/callback')).toBeInTheDocument()
    expect(screen.getByLabelText(/Client secret reference/)).toHaveValue('secret://tenant/oidc_client_secret')

    const save = screen.getByRole('button', { name: 'Save sign-in settings' })
    fireEvent.change(screen.getByLabelText('Issuer'), { target: { value: 'http://localhost:8180/realms/northwind' } })
    fireEvent.change(screen.getByLabelText('Client ID'), { target: { value: 'axyntro' } })
    expect(save).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText(/Connected: http:\/\/localhost:8180\/realms\/northwind, 1 signing key/)).toBeInTheDocument()
    expect(save).toBeEnabled()

    // Changing anything after the test needs a new test.
    fireEvent.change(screen.getByLabelText('Client ID'), { target: { value: 'axyntro-2' } })
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Client ID'), { target: { value: 'axyntro' } })
    expect(save).toBeEnabled()

    fireEvent.click(save)
    await waitFor(() => expect(api.saveIdentitySettings).toHaveBeenCalledWith(expect.objectContaining({
      auth_mode: 'oidc', issuer: 'http://localhost:8180/realms/northwind', client_id: 'axyntro',
    })))
    expect(await screen.findByText(/Saved here \(by Nora/)).toBeInTheDocument()
  })

  it('explains a failed connection test and keeps saving off', async () => {
    api.testIdentitySettings.mockResolvedValue({ ok: false, issuer: null, signing_keys: 0, authorization_endpoint: null,
      token_endpoint: null, end_session_endpoint: null, secret_configured: false,
      problems: ["Couldn't reach the IdP: connection refused"] })
    render(<DirectoryPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText(/connection test failed: Couldn't reach the IdP/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save sign-in settings' })).toBeDisabled()
  })
})

describe('Support access (Epic 38, US38.8)', () => {
  const grant = {
    id: 'g1', access: 'read', operator_email: null, reason: 'Ticket 4711', granted_by: 'Nora Lund',
    created_at: '2026-10-16T08:00:00', expires_at: '2026-10-17T08:00:00', revoked_at: null, revoked_by: null,
    active: true, live_sessions: 1,
  }

  it('grants read-only access by default, for a set time, and can revoke it', async () => {
    api.grantSupport.mockResolvedValue(grant)
    api.revokeSupport.mockResolvedValue({ ...grant, active: false })
    render(<DirectoryPage />)
    expect(await screen.findByText('No support access right now.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Ticket 4711' } })
    fireEvent.change(screen.getByLabelText('For (hours)'), { target: { value: '4' } })
    api.listSupportGrants.mockResolvedValue([grant])
    fireEvent.click(screen.getByRole('button', { name: 'Grant support access' }))
    await waitFor(() => expect(api.grantSupport).toHaveBeenCalledWith({
      access: 'read', hours: 4, operator_email: null, reason: 'Ticket 4711',
    }))
    expect(await screen.findByText(/1 support session\(s\) open/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }))
    await waitFor(() => expect(api.revokeSupport).toHaveBeenCalledWith('g1'))
  })
})
