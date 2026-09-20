import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RegistryEntry, RegistryStatus } from '../api/types'
import RegistriesPage from './RegistriesPage'

const listRegistries = vi.fn()
const searchRegistries = vi.fn()

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  listRegistries: (...args: unknown[]) => listRegistries(...args),
  searchRegistries: (...args: unknown[]) => searchRegistries(...args),
}))

const registries: RegistryStatus[] = [
  { name: 'local', type: 'local', reachable: true, authenticated: true, message: null },
]

const entry: RegistryEntry = {
  id: 'regentry-1',
  registry_name: 'local',
  agent_name: 'Invoice Fetcher Agent',
  tags: ['finance'],
  definition: { name: 'Invoice Fetcher Agent' },
  pushed_at: '2026-01-01T00:00:00Z',
  source_process_id: 'proc-1',
  source_node_ids: ['Task_a'],
  pushed_by: 'user-1',
  pushed_by_name: 'Alice',
}

describe('RegistriesPage', () => {
  beforeEach(() => {
    listRegistries.mockResolvedValue(registries)
    searchRegistries.mockResolvedValue({ entries: [entry], registry_errors: {} })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows configured registry health and an initial browse of all entries', async () => {
    render(<RegistriesPage />)

    expect(await screen.findAllByText('local')).not.toHaveLength(0)
    expect(screen.getByText('reachable')).toBeInTheDocument()
    expect(screen.getByText('authenticated')).toBeInTheDocument()
    expect(await screen.findByText('Invoice Fetcher Agent')).toBeInTheDocument()
    expect(screen.getByText('finance')).toBeInTheDocument()
  })

  it('searches on submit and shows results', async () => {
    const user = userEvent.setup()
    render(<RegistriesPage />)
    await screen.findByText('Invoice Fetcher Agent')

    searchRegistries.mockResolvedValue({ entries: [], registry_errors: {} })
    await user.type(screen.getByPlaceholderText(/search by agent name or tag/i), 'nothing-matches')
    await user.click(screen.getByRole('button', { name: /^search$/i }))

    await waitFor(() => expect(searchRegistries).toHaveBeenCalledWith('nothing-matches'))
    expect(await screen.findByText(/no registered agents found/i)).toBeInTheDocument()
  })

  it('surfaces a per-registry search error without hiding other results', async () => {
    searchRegistries.mockResolvedValue({ entries: [entry], registry_errors: { vendor: 'connection refused' } })
    render(<RegistriesPage />)

    expect(await screen.findByText('Invoice Fetcher Agent')).toBeInTheDocument()
    expect(screen.getByText(/vendor.*connection refused/i)).toBeInTheDocument()
  })

  it('expands an entry to show its full definition', async () => {
    const user = userEvent.setup()
    render(<RegistriesPage />)
    await screen.findByText('Invoice Fetcher Agent')

    await user.click(screen.getByRole('button', { name: /view definition/i }))
    expect(screen.getByText(/"name": "Invoice Fetcher Agent"/)).toBeInTheDocument()
  })
})
