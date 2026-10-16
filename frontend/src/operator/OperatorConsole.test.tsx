import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import OperatorConsole from './OperatorConsole'

const api = vi.hoisted(() => ({
  config: vi.fn(), me: vi.fn(), login: vi.fn(), logout: vi.fn(), tenants: vi.fn(), createTenant: vi.fn(),
  changeStatus: vi.fn(), grants: vi.fn(), enter: vi.fn(), audit: vi.fn(), setLimits: vi.fn(),
}))
vi.mock('./api', () => ({ operatorApi: api }))

const tenant = {
  id: 'ten_1', slug: 'northwind', display_name: 'Northwind', status: 'active', region: null, created_at: '2026-10-15T00:00:00',
  hosts: ['northwind.localhost'],
  usage: {
    users: 3, processes: 7, jobs_waiting: 0, last_sign_in_at: null, support_grants_active: 1,
    llm_calls_this_month: 40, llm_cost_this_month_usd: 12.5,
  },
  limits: { max_concurrent_jobs: null, llm_requests_per_minute: null, llm_monthly_budget_usd: 50 },
  effective_limits: { max_concurrent_jobs: 4, llm_requests_per_minute: null, llm_monthly_budget_usd: 50 },
}
const operator = { id: 'op1', name: 'Otto Operator', email: 'otto@hosting.test', csrf_token: 'c', session_expires_at: '', auth_method: 'local' }

beforeEach(() => {
  vi.clearAllMocks()
  api.config.mockResolvedValue({ auth_mode: 'local', environment: 'development', sso_login_url: null })
  api.tenants.mockResolvedValue([tenant])
  api.audit.mockResolvedValue([])
})

describe('Operator console (Epic 38, US38.8)', () => {
  it('asks an operator to sign in first', async () => {
    api.me.mockRejectedValue(new Error('401'))
    api.login.mockResolvedValue(operator)
    render(<OperatorConsole />)
    fireEvent.change(await screen.findByLabelText('Name'), { target: { value: 'Otto Operator' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Otto Operator')).toBeInTheDocument()
    expect(api.login).toHaveBeenCalledWith('Otto Operator', '')
  })

  it('lists tenants as counts and opens support only through a grant', async () => {
    api.me.mockResolvedValue(operator)
    api.grants.mockResolvedValue([{ id: 'g1', access: 'read', reason: 'Ticket 4711', granted_by: 'Nora', expires_at: '2026-10-17T08:00:00' }])
    api.enter.mockResolvedValue({ url: 'http://northwind.localhost:8000/api/auth/support/enter?token=t', expires_in_seconds: 60 })
    const assign = vi.fn()
    Object.defineProperty(window, 'location', { value: { ...window.location, assign }, writable: true })
    render(<OperatorConsole />)
    const row = (await screen.findByText('Northwind')).closest('tr')!
    expect(row).toHaveTextContent('7')
    fireEvent.click(screen.getByRole('button', { name: '1 grant(s)' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Open as support' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('http://northwind.localhost:8000/api/auth/support/enter?token=t'))
  })

  it('deletes a suspended tenant only after a confirmation', async () => {
    api.me.mockResolvedValue(operator)
    api.tenants.mockResolvedValue([{ ...tenant, status: 'suspended' }])
    api.changeStatus.mockResolvedValue({ ...tenant, status: 'deleting' })
    render(<OperatorConsole />)
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }))
    expect(api.changeStatus).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm: delete northwind' }))
    await waitFor(() => expect(api.changeStatus).toHaveBeenCalledWith('ten_1', 'delete'))
  })

  it("shows each tenant's LLM cost against its budget and sets its limits (US38.9)", async () => {
    api.me.mockResolvedValue(operator)
    api.setLimits.mockResolvedValue(tenant)
    render(<OperatorConsole />)
    const row = (await screen.findByText('Northwind')).closest('tr')!
    expect(row).toHaveTextContent('$12.50 of $50.00')
    fireEvent.click(screen.getByRole('button', { name: 'Limits' }))
    expect(screen.getByLabelText('Concurrent jobs')).toHaveAttribute('placeholder', 'default: 4')
    fireEvent.change(screen.getByLabelText('LLM requests per minute'), { target: { value: '120' } })
    fireEvent.change(screen.getByLabelText('Monthly LLM budget (USD)'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save limits' }))
    await waitFor(() =>
      expect(api.setLimits).toHaveBeenCalledWith('ten_1', {
        max_concurrent_jobs: null, llm_requests_per_minute: 120, llm_monthly_budget_usd: null,
      }),
    )
  })
})
