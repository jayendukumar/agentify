import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ContextPack } from '../../api/types'
import { CitationList, ContextPackInspector } from './ContextCitations'

const getContextPack = vi.fn()

vi.mock('../../api/client', () => ({
  ApiError: class ApiError extends Error {},
  getContextPack: (...args: unknown[]) => getContextPack(...args),
}))

const pack: ContextPack = {
  id: 'ctxpack-1',
  purpose: 'blueprint_evaluation',
  scope: { business_unit_id: null, jurisdictions: ['DE'], process_type: null, data_classes: [] },
  process_id: 'proc-1',
  entries: [
    { label: 'C1', entity_id: 'ctx-1', kind: 'control', code: 'AML-4', name: 'Sanctions review', version: 1, text: 'Control AML-4: Sanctions review. Requires human review by MLRO.' },
  ],
  passages: [],
  token_estimate: 20,
  budget: 3500,
  truncated: 1,
  created_at: '2026-10-11T00:00:00Z',
  stale_entries: [{ entity_id: 'ctx-1', kind: 'control', name: 'Sanctions review', pinned_version: 1, current_version: 2, current_status: 'confirmed' }],
  rendered: '',
}

describe('ContextCitations', () => {
  afterEach(() => vi.clearAllMocks())

  it('renders nothing without citations', () => {
    const { container } = render(<CitationList citations={[]} heading="Applicable rules" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('loads the pack on demand and flags what changed since', async () => {
    getContextPack.mockResolvedValue(pack)
    const user = userEvent.setup()
    render(<ContextPackInspector packId="ctxpack-1" />)
    expect(getContextPack).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: /show organization context used/i }))
    expect(await screen.findByText(/Requires human review by MLRO/)).toBeInTheDocument()
    expect(screen.getByText(/1 lower-priority items left out to fit/)).toBeInTheDocument()
    expect(screen.getByText(/Changed since: Sanctions review \(v1 -> 2\)/)).toBeInTheDocument()
    expect(getContextPack).toHaveBeenCalledWith('ctxpack-1')
  })
})
