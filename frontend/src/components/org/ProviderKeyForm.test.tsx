import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ProviderKeyForm } from './GovernancePanel'

const api = vi.hoisted(() => ({ getProviderKey: vi.fn(), setProviderKey: vi.fn() }))
vi.mock('../../api/client', () => ({ ApiError: class extends Error {}, ...api }))

beforeEach(() => {
  vi.clearAllMocks()
})

describe('Model provider key (Epic 38, US38.11)', () => {
  it("switches from the hosting company's key to the organization's own", async () => {
    api.getProviderKey.mockResolvedValue({ key_ref: null, key_set: false, location: null, provider: 'openrouter' })
    api.setProviderKey.mockResolvedValue({
      key_ref: 'secret://tenant/llm_provider_key', key_set: false,
      location: 'TENANT_NORTHWIND_LLM_PROVIDER_KEY (environment variable)', provider: 'openrouter',
    })
    render(<ProviderKeyForm />)
    expect(await screen.findByText(/use the hosting company's key/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText(/Your own provider key/), { target: { value: 'secret://tenant/llm_provider_key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use this key' }))
    await waitFor(() => expect(api.setProviderKey).toHaveBeenCalledWith('secret://tenant/llm_provider_key'))
    expect(await screen.findByText(/not set yet -- they are refused/)).toBeInTheDocument()
    expect(screen.getByText(/TENANT_NORTHWIND_LLM_PROVIDER_KEY/)).toBeInTheDocument()
  })
})
