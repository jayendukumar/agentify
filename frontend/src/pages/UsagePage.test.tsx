import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UsagePage from './UsagePage'
import type { UsageOverview } from '../api/types'

const api = vi.hoisted(() => ({ getUsage: vi.fn() }))
vi.mock('../api/client', () => ({ ApiError: class extends Error {}, getUsage: api.getUsage }))

const overview: UsageOverview = {
  limits: { max_concurrent_jobs: 4, llm_requests_per_minute: null, llm_monthly_budget_usd: 50 },
  budget: { month: '2026-10', spent_usd: 42, budget_usd: 50, warn_at_usd: 40, status: 'warning', own_key: false },
  jobs_running: 2,
  jobs_queued: 9,
  months: [
    {
      month: '2026-10', calls: 120, input_tokens: 1_500_000, output_tokens: 300_000, total_tokens: 1_800_000, cost_usd: 42,
      calls_without_cost: 3, own_key_cost_usd: 0,
      operations: [
        { operation: 'bpmn_generation', model: 'qwen/qwen3.7-flash', key_source: 'platform', calls: 100, total_tokens: 1_500_000, cost_usd: 40, calls_without_cost: 0 },
        { operation: 'chat_edit', model: 'qwen/qwen3.7-flash', key_source: 'tenant', calls: 20, total_tokens: 300_000, cost_usd: 2, calls_without_cost: 0 },
      ],
    },
    { month: '2026-09', calls: 10, input_tokens: 1000, output_tokens: 100, total_tokens: 1100, cost_usd: 1.25, calls_without_cost: 0, own_key_cost_usd: 0, operations: [] },
  ],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.getUsage.mockResolvedValue(overview)
})

describe('Usage page (Epic 38, US38.9)', () => {
  it('shows the month against its budget, with the warning past 80%', async () => {
    render(<UsagePage />)
    const month = await screen.findByRole('region', { name: 'This month' })
    expect(within(month).getByText('$42.00')).toBeInTheDocument()
    expect(screen.getByText(/Over 80% of the monthly budget/)).toBeInTheDocument()
    expect(screen.getByText('4 (2 running, 9 queued)')).toBeInTheDocument()
    expect(screen.getByText('No limit')).toBeInTheDocument()
  })

  it('lists cost per month, and per operation on request', async () => {
    render(<UsagePage />)
    const september = (await screen.findByText('2026-09')).closest('tr')!
    expect(september).toHaveTextContent('$1.25')
    expect(screen.getByText(/3 call\(s\) without a price/)).toBeInTheDocument()
    expect(screen.queryByText('bpmn_generation')).not.toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'By operation' })[0])
    expect(screen.getByText('bpmn_generation')).toBeInTheDocument()
  })

  it('says when the budget is used up', async () => {
    api.getUsage.mockResolvedValue({ ...overview, budget: { ...overview.budget, spent_usd: 50, status: 'stopped' } })
    render(<UsagePage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('used up')
  })

  it('shows what was spent on the organization\'s own key (US38.11)', async () => {
    api.getUsage.mockResolvedValue({
      ...overview,
      budget: { ...overview.budget, own_key: true },
      months: [{ ...overview.months[0], own_key_cost_usd: 2 }],
    })
    render(<UsagePage />)
    expect(await screen.findByText(/no\s+longer limits them/)).toBeInTheDocument()
    expect(screen.getByText(/\$2\.00 on your own key/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'By operation' }))
    expect(screen.getByText(/tokens, your own key/)).toBeInTheDocument()
  })
})
