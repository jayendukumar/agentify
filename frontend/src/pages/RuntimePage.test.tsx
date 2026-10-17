import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RuntimePage from './RuntimePage'
import type { Rate, RuntimeMetrics, RuntimeOverviewRow } from '../api/types'

const api = vi.hoisted(() => ({
  list: vi.fn(), comparison: vi.fn(), promote: vi.fn(), rollback: vi.fn(), drift: vi.fn(), toDataset: vi.fn(),
  monitoring: vi.fn(), executions: vi.fn(), execution: vi.fn(), datasets: vi.fn(),
}))
vi.mock('../api/client', () => ({
  ApiError: class extends Error {},
  listRuntimeDeployments: api.list,
  getRolloutComparison: api.comparison,
  promoteDeployment: api.promote,
  rollbackDeployment: api.rollback,
  getDrift: api.drift,
  driftCasesToDataset: api.toDataset,
  updateMonitoring: api.monitoring,
  listDeploymentExecutions: api.executions,
  getRuntimeExecution: api.execution,
  listDatasets: api.datasets,
}))
vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ user: { name: 'ed', roles: [] }, can: () => true }) }))

const rate = (passed: number, total: number): Rate => ({
  passed, total, rate: total ? passed / total : null, low: total ? 0 : null, high: total ? 1 : null,
})

const metrics: RuntimeMetrics = {
  window: 'day', since: '2026-10-16T10:00:00', executions: 12, by_status: { COMPLETED: 11, FAILED: 1 }, finished: 12,
  error_rate: rate(1, 12), escalation_rate: rate(0, 12), latency_seconds: { p50: 4.2, p95: 9.8 }, cost_usd: 0.12,
  cost_per_case_usd: 0.01, tokens: 9000, tools: { 'Core Banking': { calls: 20, errors: 1, not_sent: 0 } },
  tool_errors: 1, policy_denials: 0, escalations: 0,
  reviews: { opened: 0, open: 0, approved: 0, rejected: 0, reject_rate: rate(0, 0), wait_minutes_median: null, waiting_now_minutes_max: 0 },
  stops: [], shadow_copies: 0,
}

function row(overrides: Partial<RuntimeOverviewRow['deployment']> = {}): RuntimeOverviewRow {
  return {
    deployment: {
      id: 'dep_2', process_id: 'proc_1', process_name: 'Loan onboarding', agent_artifact_id: 'agent_1',
      agent_name: 'Loan Onboarding Agent', artifact_version: 2, status: 'active', traffic_share: 25,
      shadow_started_at: null, previous_deployment_id: 'dep_1', activated_at: '2026-10-17T08:00:00', stop_reason: null,
      pause_reason: null, alert_rules: [], telemetry: {},
      drift_rule: { enabled: true, action: 'notify', window_days: 7, min_executions: 100 }, has_baseline: true,
      ...overrides,
    },
    metrics,
    drift: { status: 'input_drift', flagged: ['input:segment'], reading: 'The cases changed', checked_at: '2026-10-17T09:00:00', cases: 120 },
    alerts: [],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue([row()])
  api.comparison.mockResolvedValue({
    deployment_id: 'dep_2', version: 2, against: { deployment_id: 'dep_1', version: 1, status: 'active' },
    stage: 'canary 25%', decision_field: 'decision',
    numbers: {
      new: { cases: 3, decision_mix: { approve: 3 }, escalation_rate: rate(0, 3), error_rate: rate(0, 3), cost_per_case_usd: 0.01 },
      old: { cases: 9, decision_mix: { approve: 9 }, escalation_rate: rate(1, 9), error_rate: rate(0, 9), cost_per_case_usd: 0.02 },
    },
    paired: null, reading: "A canary's cases don't overlap the stable version's", method: 'Wilson',
  })
  api.drift.mockResolvedValue({
    status: 'input_drift', reading: 'The cases changed (segment), but the agent still behaves as in the twin.',
    checked_at: '2026-10-17T09:00:00', window_days: 7, min_executions: 100, action: 'notify', cases: 120,
    baseline: { available: true, runs: 40, pass_rate: rate(38, 40) },
    behaviour: [], inputs: [{ metric: 'input:segment', baseline: {}, live: {}, statistic: 'PSI 0.9', flagged: true, reading: "'segment' looks different" }],
  })
  api.executions.mockResolvedValue([])
  api.datasets.mockResolvedValue([])
  api.promote.mockResolvedValue({})
})

function renderAt(path = '/runtime') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <RuntimePage />
    </MemoryRouter>,
  )
}

describe('Runtime page (Epic 39, step 4)', () => {
  it('lists deployments with their stage, numbers and drift', async () => {
    renderAt()
    expect(await screen.findByRole('button', { name: 'Loan Onboarding Agent v2' })).toBeInTheDocument()
    expect(screen.getByText('Canary 25%')).toBeInTheDocument()
    expect(screen.getByText('8%')).toBeInTheDocument()
    expect(screen.getByText('Cases changed')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Last week' }))
    await waitFor(() => expect(api.list).toHaveBeenLastCalledWith('week'))
  })

  it('shows the comparison and steps a canary up', async () => {
    renderAt('/runtime?deployment=dep_2')
    expect(await screen.findByText(/don't overlap the stable version's/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Take 5% of cases' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Take all cases' }))
    await waitFor(() => expect(api.promote).toHaveBeenCalledWith('proc_1', 'dep_2', 100))
    expect(await screen.findByText(/'segment' looks different/)).toBeInTheDocument()
  })

  it('asks why before rolling back', async () => {
    api.rollback.mockResolvedValue({})
    renderAt('/runtime?deployment=dep_2')
    const button = await screen.findByRole('button', { name: 'Roll back' })
    expect(button).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Why roll back'), { target: { value: 'Escalates too often' } })
    fireEvent.click(button)
    await waitFor(() => expect(api.rollback).toHaveBeenCalledWith('proc_1', 'dep_2', 'Escalates too often'))
  })
})
