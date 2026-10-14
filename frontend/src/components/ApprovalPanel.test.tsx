import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { ApprovalStatus } from '../api/types'
import ApprovalPanel from './ApprovalPanel'

const base: ApprovalStatus = {
  object_type: 'agent',
  object_id: 'agent-1',
  status: 'pending_approval',
  required_approvals: 2,
  approvals: [{ approver_id: 'u2', approver_name: 'Priya', decision: 'approved', comment: null, at: null }],
  changes: [
    { actor_id: 'u1', actor_name: 'Sam', action: 'governance_edited', label: 'edited the agent definition', tier: 'high', at: null },
    { actor_id: 'u3', actor_name: 'Lee', action: 'create_scenario', label: 'create scenario', tier: 'standard', at: null },
  ],
  can_approve: true,
  reason: null,
}

describe('ApprovalPanel', () => {
  it('lists high-risk changes and approves once', async () => {
    const load = vi.fn().mockResolvedValue(base)
    const onApprove = vi.fn().mockResolvedValue(undefined)
    render(<ApprovalPanel load={load} onApprove={onApprove} />)

    expect(await screen.findByText(/Sam edited the agent definition/)).toBeInTheDocument()
    expect(screen.getByText(/1 of 2 approvals \(Priya\)/)).toBeInTheDocument()
    expect(screen.getByText(/Other activity \(1\)/)).toBeInTheDocument()

    await userEvent.setup().click(screen.getByRole('button', { name: 'Approve' }))
    expect(onApprove).toHaveBeenCalledWith('')
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
  })

  it('shows why the signed-in user cannot approve instead of the button', async () => {
    const load = vi.fn().mockResolvedValue({
      ...base,
      can_approve: false,
      reason: 'You edited the agent definition on 12 Oct 2026 -- someone else must approve this agent version',
    })
    render(<ApprovalPanel load={load} onApprove={vi.fn()} />)

    expect(await screen.findByRole('note')).toHaveTextContent(/someone else must approve/)
    expect(screen.queryByRole('button', { name: 'Approve' })).not.toBeInTheDocument()
    // A re-render doesn't refetch on its own.
    expect(load).toHaveBeenCalledTimes(1)
  })
})
