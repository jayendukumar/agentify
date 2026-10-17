import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import InboxPage from './InboxPage'
import type { ReviewTask } from '../api/types'

const api = vi.hoisted(() => ({ list: vi.fn(), decide: vi.fn(), resolve: vi.fn(), notes: vi.fn() }))
vi.mock('../api/client', () => ({
  ApiError: class extends Error {},
  listInbox: api.list,
  decideReviewTask: api.decide,
  resolveReviewTask: api.resolve,
  listRuntimeNotifications: api.notes,
}))

const checkpoint: ReviewTask = {
  id: 'rtask_1', execution_id: 'exe_1', deployment_id: 'dep_1', process_id: 'proc_1', agent_name: 'Loan Onboarding Agent',
  kind: 'checkpoint', status: 'open', summary: 'Pay 500 EUR?', proposed_action: { amount: 500 }, case_input: { customerId: 'C-1' },
  assignee_role: 'Team Lead', level: 0, due_at: '2026-10-17T12:00:00', overdue: true, escalated_at: null, decision: null,
  outcome: null, comment: null, decided_by: null, decided_at: null, created_at: '2026-10-17T08:00:00',
}

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue([checkpoint])
  api.notes.mockResolvedValue([])
})

describe('Inbox page (Epic 39, US39.10)', () => {
  it('shows a checkpoint with who should take it and the proposed action', async () => {
    render(<InboxPage />)
    expect(await screen.findByText('Pay 500 EUR?')).toBeInTheDocument()
    expect(screen.getByText(/For Team Lead/)).toBeInTheDocument()
    expect(screen.getByText('Overdue')).toBeInTheDocument()
    expect(screen.getByText('amount').nextSibling).toHaveTextContent('500')
  })

  it('asks for a reason before rejecting, then approves', async () => {
    api.decide.mockResolvedValue({ ...checkpoint, status: 'decided', decision: 'approve' })
    render(<InboxPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Say why')
    expect(api.decide).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Comment'), { target: { value: 'Within budget' } })
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(api.decide).toHaveBeenCalledWith('proc_1', 'rtask_1', 'approve', 'Within budget'))
    expect(await screen.findByText('Nothing is waiting for you.')).toBeInTheDocument()
  })

  it('resolves an escalated case with the outcome recorded', async () => {
    api.list.mockResolvedValue([{ ...checkpoint, kind: 'escalation', summary: 'Exceeded max_steps' }])
    api.resolve.mockResolvedValue({ ...checkpoint, kind: 'escalation', status: 'resolved' })
    render(<InboxPage />)
    fireEvent.change(await screen.findByLabelText('Outcome'), { target: { value: 'approve' } })
    fireEvent.change(screen.getByLabelText('Outcome details (JSON)'), { target: { value: '{"riskRating": "low"}' } })
    fireEvent.click(screen.getByRole('button', { name: 'Resolve case' }))
    await waitFor(() =>
      expect(api.resolve).toHaveBeenCalledWith('proc_1', 'rtask_1', { riskRating: 'low', decision: 'approve' }, ''),
    )
  })
})
