import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ContextEntity } from '../../api/types'
import PolicyReviewPanel from './PolicyReviewPanel'

const listPolicyDocuments = vi.fn()
const listContextEntities = vi.fn()
const confirmContextEntity = vi.fn()
const dismissContextEntity = vi.fn()
const bulkConfirmContextEntities = vi.fn()

vi.mock('../../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  listPolicyDocuments: (...args: unknown[]) => listPolicyDocuments(...args),
  listContextEntities: (...args: unknown[]) => listContextEntities(...args),
  confirmContextEntity: (...args: unknown[]) => confirmContextEntity(...args),
  dismissContextEntity: (...args: unknown[]) => dismissContextEntity(...args),
  bulkConfirmContextEntities: (...args: unknown[]) => bulkConfirmContextEntities(...args),
  uploadPolicyDocument: vi.fn(),
}))

function proposal(overrides: Partial<ContextEntity>): ContextEntity {
  return {
    id: 'ctx-1',
    kind: 'control',
    name: 'Sanctions matches reviewed by MLRO',
    code: 'SAN-1',
    data: { description: 'Potential matches go to the MLRO.', requires_human_review: true, review_role: 'MLRO' },
    applies_to: { business_units: [], jurisdictions: ['EU'], data_classes: [], process_types: [], systems: [] },
    status: 'proposed',
    confidence: 0.9,
    citations: [{ passage_id: 'p1', policy_id: 'pol1', clause_ref: 'paragraph 3', quote: 'Any potential sanctions match must be reviewed by the MLRO.' }],
    source: 'document:d1',
    parent_id: null,
    supersedes_id: null,
    relaxes: false,
    owner_business_unit_id: null,
    version: 1,
    created_at: '2026-10-11T00:00:00Z',
    updated_at: '2026-10-11T00:00:00Z',
    confirmed_by: null,
    confirmed_at: null,
    ...overrides,
  }
}

describe('PolicyReviewPanel', () => {
  beforeEach(() => {
    listPolicyDocuments.mockResolvedValue([])
    confirmContextEntity.mockResolvedValue({})
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.restoreAllMocks()
  })

  it('shows extracted rules with their citation and confirms one', async () => {
    listContextEntities.mockResolvedValue([proposal({})])
    const user = userEvent.setup()
    render(<PolicyReviewPanel businessUnits={[]} canEdit onChanged={() => {}} />)

    expect(await screen.findByText('Sanctions matches reviewed by MLRO')).toBeInTheDocument()
    expect(screen.getByText(/Any potential sanctions match must be reviewed/)).toBeInTheDocument()
    expect(screen.getByText('90% confident')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(confirmContextEntity).toHaveBeenCalledWith('ctx-1', false))
  })

  it('asks before confirming a rule that loosens an existing one', async () => {
    listContextEntities.mockResolvedValue([proposal({ relaxes: true, supersedes_id: 'ctx-0' })])
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    render(<PolicyReviewPanel businessUnits={[]} canEdit onChanged={() => {}} />)

    expect(await screen.findByText('Loosens an existing rule')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    expect(confirmSpy).toHaveBeenCalled()
    expect(confirmContextEntity).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    await user.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(confirmContextEntity).toHaveBeenCalledWith('ctx-1', true))
  })

  it('reports what bulk confirm left for individual review', async () => {
    listContextEntities.mockResolvedValue([proposal({}), proposal({ id: 'ctx-2', name: 'Low', confidence: 0.4 })])
    bulkConfirmContextEntities.mockResolvedValue({ confirmed: ['ctx-1'], skipped: { 'ctx-2': 'low confidence (0.40)' } })
    const user = userEvent.setup()
    render(<PolicyReviewPanel businessUnits={[]} canEdit onChanged={() => {}} />)

    await user.click(await screen.findByRole('button', { name: 'Confirm all high-confidence' }))
    expect(await screen.findByText(/Confirmed 1\. 1 left for individual review/)).toBeInTheDocument()
  })
})
