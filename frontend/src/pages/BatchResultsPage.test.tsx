import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { BatchResults, BatchView } from '../api/types'
import BatchResultsPage from './BatchResultsPage'

vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ user: { name: 'ed', roles: [], permissions: [] }, can: () => true }) }))

const batch: BatchView = {
  id: 'b1', scenario_id: 's1', agent_artifact_id: 'a1', agent_version: 2, dataset_id: 'd1', dataset_version: 3,
  mode: 'correctness', tier: 'regression', trials: 2, rows: 4, status: 'completed', budget_usd: null, spent_usd: 0.4,
  estimate: { rows: 4, trials: 2, runs: 8, cost_per_run_usd: 0.05, seconds_per_run: 10, total_cost_usd: 0.4, total_minutes: 1, basis: 'x' },
  load_settings: {}, progress: { total: 8, finished: 8, passed: 5, failed: 3, errored: 0, cancelled: 0 },
  created_at: '2026-10-11T00:00:00Z', finished_at: null,
}

const results: BatchResults = {
  batch_id: 'b1', mode: 'correctness', status: 'completed',
  headline: { passed: 5, total: 8, rate: 0.625, low: 0.31, high: 0.86 },
  reading: 'The agent handles about 62 in 100 cases correctly (between 31 and 86, with 95% confidence, from 8 graded runs).',
  excluded: {},
  segments: [{ segment: 'edge', rate: { passed: 2, total: 4, rate: 0.5, low: 0.15, high: 0.85 }, weak: true }],
  provenance: [],
  decision_matrix: { approve: { approve: 3, reject: 3 } },
  field_accuracy: {},
  clusters: [{ pattern: "Expected the case to be decided '…'", count: 3, example_reason: "Expected the case to be decided 'approve', but it was 'reject'", row_keys: ['a2', 'b2'], run_ids: [] }],
  consistency: { trials: 2, always_pass: 2, always_fail: 1, inconsistent: 1, pass_at_k: 0.75, pass_hat_k: 0.5, inconsistent_rows: ['a2'] },
  cost_usd: { median: 0.05, p95: 0.06, max: 0.07 }, tokens: { median: 1000, p95: 1200, max: 1300 }, seconds: { median: 10, p95: 12, max: 13 },
  worst_rows: [], relations: [], load: null, critical_failures: ['b2'],
}

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {},
  getBatch: () => Promise.resolve(batch),
  getBatchResults: () => Promise.resolve(results),
  listBatches: () => Promise.resolve([]),
  getReleaseCriteria: () => Promise.resolve(null),
  getReleaseGate: () => Promise.resolve({ passed: false, batch_id: 'b1', reasons: ['1 critical case(s) failed: b2'], checked_at: '' }),
  compareBatches: vi.fn(),
  getBatchEvidence: vi.fn(),
  setReleaseCriteria: vi.fn(),
}))

describe('BatchResultsPage', () => {
  it('leads with the pass rate, its confidence, and what goes wrong', async () => {
    render(
      <MemoryRouter initialEntries={['/processes/p1/batches/b1']}>
        <Routes><Route path="/processes/:processId/batches/:batchId" element={<BatchResultsPage />} /></Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText('63% correct')).toBeInTheDocument()
    expect(screen.getByText(/between 31 and 86, with 95% confidence/)).toBeInTheDocument()
    expect(screen.getByText('Critical cases failed: b2')).toBeInTheDocument()
    expect(screen.getByText('weak')).toBeInTheDocument()
    expect(screen.getByText(/but it was 'reject'/)).toBeInTheDocument()
    expect(screen.getByText(/1 sometimes right/)).toBeInTheDocument()
    expect(screen.getByText(/Not ready: 1 critical case\(s\) failed: b2/)).toBeInTheDocument()
  })
})
