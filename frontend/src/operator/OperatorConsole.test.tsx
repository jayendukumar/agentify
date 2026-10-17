import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import OperatorConsole from './OperatorConsole'

const api = vi.hoisted(() => ({
  config: vi.fn(), me: vi.fn(), login: vi.fn(), logout: vi.fn(), tenants: vi.fn(), createTenant: vi.fn(),
  changeStatus: vi.fn(), grants: vi.fn(), enter: vi.fn(), audit: vi.fn(), setLimits: vi.fn(), invite: vi.fn(),
  erase: vi.fn(), certificates: vi.fn(),
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
  created_via: 'console',
  first_admin: { state: 'invited', email: 'ada@northwind.test', expires_at: '2026-10-17T08:00:00' },
  suspended_at: null,
  delete_after: null,
  last_export: null,
  dedicated_database: false,
}
const certificate = {
  id: 'paudit_1', occurred_at: '2026-10-16T09:00:00', operator_name: 'Otto Operator', tenant_id: 'ten_old',
  certificate: { slug: 'oldco', erased_at: '2026-10-16T09:00:00', rows_total: 812, files_removed: 4, tables: { processes: 3 }, secrets: {} },
}
const operator = { id: 'op1', name: 'Otto Operator', email: 'otto@hosting.test', csrf_token: 'c', session_expires_at: '', auth_method: 'local' }

beforeEach(() => {
  vi.clearAllMocks()
  api.config.mockResolvedValue({ auth_mode: 'local', environment: 'development', sso_login_url: null, region: '', regions: {} })
  api.tenants.mockResolvedValue([tenant])
  api.audit.mockResolvedValue([])
  api.certificates.mockResolvedValue([])
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

  it('creates a tenant with an invitation for its first admin and shows the link once (US38.12)', async () => {
    api.me.mockResolvedValue(operator)
    api.createTenant.mockResolvedValue({
      tenant: { ...tenant, slug: 'acme', first_admin: { state: 'invited', email: 'ada@acme.test', expires_at: null } },
      invitation_url: 'http://acme.localhost:8000/api/auth/invitation/accept?token=t1',
      invitation_expires_at: '2026-10-17T08:00:00',
    })
    render(<OperatorConsole />)
    fireEvent.change(await screen.findByLabelText('Slug'), { target: { value: 'acme' } })
    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'acme.localhost' } })
    fireEvent.change(screen.getByLabelText("First admin's email"), { target: { value: 'ada@acme.test' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create tenant' }))
    await waitFor(() =>
      expect(api.createTenant).toHaveBeenCalledWith({
        slug: 'acme', display_name: '', host: 'acme.localhost', region: null, first_admin_email: 'ada@acme.test',
      }),
    )
    expect(await screen.findByLabelText('Invitation URL')).toHaveValue('http://acme.localhost:8000/api/auth/invitation/accept?token=t1')
  })

  it("connects the tenant's IdP instead, with its admins group", async () => {
    api.me.mockResolvedValue(operator)
    api.createTenant.mockResolvedValue({ tenant, invitation_url: null, invitation_expires_at: null })
    render(<OperatorConsole />)
    fireEvent.change(await screen.findByLabelText('Slug'), { target: { value: 'acme' } })
    fireEvent.change(screen.getByLabelText('Host'), { target: { value: 'acme.localhost' } })
    fireEvent.click(screen.getByLabelText(/Connect their identity provider/))
    fireEvent.change(screen.getByLabelText('Issuer'), { target: { value: 'https://login.acme.example' } })
    fireEvent.change(screen.getByLabelText('Client ID'), { target: { value: 'axyntro' } })
    fireEvent.change(screen.getByLabelText('Admins group'), { target: { value: 'acme-admins' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create tenant' }))
    await waitFor(() => expect(api.createTenant).toHaveBeenCalled())
    const body = api.createTenant.mock.calls[0][0]
    expect(body.idp).toMatchObject({ issuer: 'https://login.acme.example', client_id: 'axyntro', admin_group: 'acme-admins' })
    expect(body.first_admin_email).toBeUndefined()
    expect(screen.queryByLabelText('Invitation URL')).not.toBeInTheDocument()
  })

  it('sends a new invitation while the first admin has not joined', async () => {
    api.me.mockResolvedValue(operator)
    api.invite.mockResolvedValue({ tenant, invitation_url: 'http://northwind.localhost:8000/api/auth/invitation/accept?token=t2', invitation_expires_at: null })
    render(<OperatorConsole />)
    const row = (await screen.findByText('Northwind')).closest('tr')!
    expect(row).toHaveTextContent('Invited (ada@northwind.test)')
    fireEvent.click(screen.getByRole('button', { name: 'New invitation' }))
    await waitFor(() => expect(api.invite).toHaveBeenCalledWith('ten_1', 'ada@northwind.test'))
    expect(await screen.findByLabelText('Invitation URL')).toHaveValue('http://northwind.localhost:8000/api/auth/invitation/accept?token=t2')
  })

  it('waits out the deletion period, can restore, then erases after the slug is typed (US38.13)', async () => {
    api.me.mockResolvedValue(operator)
    api.tenants.mockResolvedValue([
      { ...tenant, status: 'deleting', suspended_at: '2026-09-01T00:00:00', delete_after: '2999-01-01T00:00:00' },
    ])
    api.changeStatus.mockResolvedValue({ ...tenant, status: 'suspended' })
    const { unmount } = render(<OperatorConsole />)
    const row = (await screen.findByText('Northwind')).closest('tr')!
    expect(row).toHaveTextContent('erasable from')
    expect(row).toHaveTextContent('No data export made')
    expect(screen.queryByRole('button', { name: 'Erase' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }))
    await waitFor(() => expect(api.changeStatus).toHaveBeenCalledWith('ten_1', 'restore'))
    unmount()

    api.tenants.mockResolvedValue([{
      ...tenant, status: 'deleting', delete_after: '2026-01-01T00:00:00',
      last_export: { status: 'downloaded', created_at: '2026-09-01T00:00:00', downloaded_at: '2026-09-01T01:00:00' },
    }])
    api.erase.mockResolvedValue({ ...certificate, tenant_id: 'ten_1', certificate: { ...certificate.certificate, slug: 'northwind' } })
    render(<OperatorConsole />)
    expect((await screen.findByText('Northwind')).closest('tr')!).toHaveTextContent('Data export downloaded')
    fireEvent.click(screen.getByRole('button', { name: 'Erase' }))
    const erase = screen.getByRole('button', { name: 'Erase northwind' })
    expect(erase).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Type northwind to confirm'), { target: { value: 'northwind' } })
    fireEvent.click(erase)
    await waitFor(() => expect(api.erase).toHaveBeenCalledWith('ten_1', 'northwind'))
    expect(await screen.findByRole('status', { name: 'Tenant erased' })).toHaveTextContent('812 rows and 4 files')
  })

  it('lists deletion certificates, with the full certificate on request', async () => {
    api.me.mockResolvedValue(operator)
    api.certificates.mockResolvedValue([certificate])
    render(<OperatorConsole />)
    const section = await screen.findByRole('region', { name: 'Deletion certificates' })
    await waitFor(() => expect(section).toHaveTextContent('oldco'))
    expect(section).toHaveTextContent('812 rows, 4 files')
    fireEvent.click(screen.getByRole('button', { name: 'Certificate' }))
    expect(section).toHaveTextContent('"processes": 3')
  })

  it("says which region this deployment serves and links the others' consoles (US38.15)", async () => {
    api.me.mockResolvedValue(operator)
    api.config.mockResolvedValue({
      auth_mode: 'local', environment: 'development', sso_login_url: null, region: 'EU',
      regions: { EU: 'https://ops.eu.example', US: 'https://ops.us.example' },
    })
    api.tenants.mockResolvedValue([{ ...tenant, region: 'EU', dedicated_database: true }])
    render(<OperatorConsole />)
    const region = await screen.findByLabelText('Deployment region')
    expect(region).toHaveTextContent('serves region EU')
    expect(screen.getByRole('link', { name: 'US' })).toHaveAttribute('href', 'https://ops.us.example')
    expect(screen.queryByRole('link', { name: 'EU' })).not.toBeInTheDocument()
    expect((await screen.findByText('Northwind')).closest('tr')!).toHaveTextContent('own database')
  })
})
