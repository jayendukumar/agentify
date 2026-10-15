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
}))

vi.mock('../api/client', async (importOriginal) => ({ ...(await importOriginal<object>()), ...api }))

const setup = { base_url: 'http://localhost:8000/scim/v2', link_claim: 'sub', tokens: [] }

beforeEach(() => {
  vi.clearAllMocks()
  api.getScimSetup.mockResolvedValue(setup)
  api.listGroupMappings.mockResolvedValue([{ group: 'axyntro-approvers', role: 'approver', scope_type: 'organization', scope_id: '' }])
  api.listKnownGroups.mockResolvedValue([{ name: 'axyntro-approvers', external_id: null, source: 'scim', members: 2 }])
  api.listContextEntities.mockResolvedValue([])
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
