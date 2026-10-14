import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { EfficiencyReport, OperatingModelView, ProcessTwinBatch } from '../api/types'
import ProcessTwinPage from './ProcessTwinPage'

vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ user: { name: 'ed', roles: [], permissions: [] }, can: () => true }) }))

const rate = (passed: number, total: number) => ({ passed, total, rate: passed / total, low: 0.5, high: 0.95 })
const interval = (mean: number) => ({ mean, low: mean * 0.9, high: mean * 1.1 })

const model: OperatingModelView = {
  process_id: 'p1', version: 2, is_default: false, updated_at: null, currency: 'EUR', monthly_volume: 9000,
  productive_hours_per_fte: 1600, working_minutes_per_day: 480, roles: [{ role: 'AP Team', hourly_cost: 48 }],
  nodes: [{ node_id: 'Task_t1', label: 'Capture invoice', role: 'AP Team', handling_minutes: { min: 3, likely: 6, max: 12 },
    wait_minutes: null, hourly_cost: null, review_minutes: null, to_be_wait_minutes: null }],
  end_event_outcomes: { Event_e3: 'approve' }, outcome_label: 'decision', usd_to_currency: 0.92,
  default_review_minutes: { min: 1, likely: 3, max: 6 },
}
const batch: ProcessTwinBatch = {
  id: 'b1', process_id: 'p1', name: 'October invoices', scenario_id: 's1', dataset_id: 'd1', dataset_version: 1,
  status: 'completed', budget_usd: 3, seed: 7, cases: 30, finished: 30, spent_usd: 0.42, operating_model_version: 2,
  created_at: '2026-10-13T10:00:00Z', finished_at: '2026-10-13T10:20:00Z',
}
const report: EfficiencyReport = {
  cases: 30, completed: 29, failed: 1, currency: 'EUR', straight_through: rate(18, 30), accuracy: rate(24, 30),
  as_is_cycle_minutes: interval(2400), to_be_cycle_minutes: interval(300), cycle_time_reduction: interval(0.87),
  as_is_touch_minutes: interval(40), to_be_touch_minutes: interval(8), touch_time_reduction: interval(0.8),
  as_is_cost_per_case: interval(34), to_be_cost_per_case: interval(7.5), llm_cost_per_case_usd: interval(0.014),
  annual_hours_released: interval(51840), annual_fte_released: interval(32.4), annual_cost_saved: interval(2862000),
  assumptions: ['Sampled from the operating model (v2).'], headline: 'Over 30 cases the to-be process finished 29.',
  nodes: [{ node_id: 'Task_t1', label: 'Capture invoice', kind: 'agent', visits: 30, failures: 1, mean_agent_seconds: 21.5,
    review_rate: 0.1, mean_llm_cost_usd: 0.002, as_is_touch_minutes: 6, to_be_touch_minutes: 0.3 }],
  segments: [
    { segment: 'duplicate', cases: 3, accuracy: rate(1, 3), straight_through: 0, mean_touch_saved_minutes: 2 },
    { segment: 'clean_small', cases: 15, accuracy: rate(15, 15), straight_through: 0.9, mean_touch_saved_minutes: 30 },
  ],
  outcome_matrix: [{ expected: 'escalate', actual: 'approve', count: 2 }, { expected: 'approve', actual: 'approve', count: 22 }],
  path_variants: [{ path: ['Task_t1', 'Event_e3'], cases: 22 }],
  cases_detail: [], usd_to_currency: 0.92,
}

const saveOperatingModel = vi.fn(async (_pid: string, m: OperatingModelView) => ({ ...m, version: 3 }))
const startProcessTwin = vi.fn(async () => batch)

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {},
  getOperatingModel: async () => model,
  saveOperatingModel: (...args: [string, OperatingModelView]) => saveOperatingModel(...args),
  listOrchestrationScenarios: async () => [{ id: 's1', name: 'Whole process' }],
  listDatasets: async () => [{ id: 'd1', name: 'Invoices', row_count: 75 }],
  listProcessTwinBatches: async () => [batch],
  getProcessTwinReport: async () => report,
  estimateProcessTwin: async () => ({ cases: 30, cost_per_case_usd: 0.014, total_cost_usd: 0.42, basis: 'recent runs' }),
  startProcessTwin: (...args: unknown[]) => startProcessTwin(...(args as [])),
  controlProcessTwin: async () => batch,
}))

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/processes/p1/process-twin']}>
      <Routes><Route path="/processes/:processId/process-twin" element={<ProcessTwinPage />} /></Routes>
    </MemoryRouter>,
  )
}

describe('ProcessTwinPage', () => {
  it('shows the efficiency report with as-is vs to-be and weak segments flagged', async () => {
    renderPage()
    expect(await screen.findByRole('heading', { name: '80% less people time per case' })).toBeInTheDocument()
    expect(screen.getByText('Over 30 cases the to-be process finished 29.')).toBeInTheDocument()
    expect(screen.getByText('32.4')).toBeInTheDocument() // FTE released
    const segments = screen.getByRole('heading', { name: 'By kind of case' }).closest('section')!
    expect(within(segments).getByText('duplicate').closest('tr')).toHaveClass('results-weak')
    expect(within(segments).getByText('clean small').closest('tr')).not.toHaveClass('results-weak')
    const steps = screen.getByRole('heading', { name: 'Step by step' }).closest('section')!
    expect(within(steps).getByText('Capture invoice').closest('tr')).toHaveClass('results-weak') // it failed once
  })

  it('saves an edited operating model and starts a run with the chosen budget', async () => {
    const user = userEvent.setup()
    renderPage()
    const volume = await screen.findByLabelText('Cases per month')
    await user.clear(volume)
    await user.type(volume, '12000')
    await user.click(screen.getByRole('button', { name: 'Save operating model' }))
    await waitFor(() => expect(saveOperatingModel).toHaveBeenCalled())
    expect(saveOperatingModel.mock.calls[0][1].monthly_volume).toBe(12000)

    await user.click(screen.getByRole('button', { name: 'Run the process twin' }))
    await waitFor(() => expect(startProcessTwin).toHaveBeenCalledWith('p1', expect.objectContaining({ scenario_id: 's1', dataset_id: 'd1', budget_usd: 2 })))
  })
})
