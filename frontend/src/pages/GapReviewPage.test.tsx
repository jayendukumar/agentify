import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { GapFinding } from '../api/types'
import GapReviewPage from './GapReviewPage'

const listGapFindings = vi.fn()
const analyzeGaps = vi.fn()
const resolveGapFinding = vi.fn()
const dismissGapFinding = vi.fn()

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  listGapFindings: (...args: unknown[]) => listGapFindings(...args),
  analyzeGaps: (...args: unknown[]) => analyzeGaps(...args),
  resolveGapFinding: (...args: unknown[]) => resolveGapFinding(...args),
  dismissGapFinding: (...args: unknown[]) => dismissGapFinding(...args),
}))

const openFinding: GapFinding = {
  id: 'gap-1',
  process_id: 'proc-1',
  kind: 'structural',
  question: "'Classify requirements' has no step before it -- is this where the process starts?",
  target_element_ids: ['a'],
  options: [
    { label: 'Yes, add a start event', diff: { intent: 'add_node', summary: 's', target_element_ids: [], operations: [] } },
    { label: 'Leave as-is', diff: null },
  ],
  status: 'open',
  chosen_option_label: null,
  created_at: '2026-01-01T00:00:00Z',
  decided_at: null,
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/processes/proc-1/gaps']}>
      <Routes>
        <Route path="/processes/:processId/gaps" element={<GapReviewPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('GapReviewPage', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows an empty state when there are no findings yet', async () => {
    listGapFindings.mockResolvedValue([])
    renderPage()

    expect(await screen.findByText(/no gaps found yet/i)).toBeInTheDocument()
  })

  it('lists an open finding with its options', async () => {
    listGapFindings.mockResolvedValue([openFinding])
    renderPage()

    expect(await screen.findByText(/has no step before it/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /yes, add a start event/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /leave as-is/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^dismiss$/i })).toBeInTheDocument()
  })

  it('resolves a finding by clicking an option', async () => {
    listGapFindings.mockResolvedValueOnce([openFinding]).mockResolvedValueOnce([
      { ...openFinding, status: 'resolved', chosen_option_label: 'Yes, add a start event' },
    ])
    resolveGapFinding.mockResolvedValue({ ...openFinding, status: 'resolved' })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/has no step before it/i)
    await user.click(screen.getByRole('button', { name: /yes, add a start event/i }))

    await waitFor(() => expect(resolveGapFinding).toHaveBeenCalledWith('proc-1', 'gap-1', 0))
  })

  it('dismisses a finding', async () => {
    listGapFindings.mockResolvedValueOnce([openFinding]).mockResolvedValueOnce([{ ...openFinding, status: 'dismissed' }])
    dismissGapFinding.mockResolvedValue({ ...openFinding, status: 'dismissed' })
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/has no step before it/i)
    await user.click(screen.getByRole('button', { name: /^dismiss$/i }))

    await waitFor(() => expect(dismissGapFinding).toHaveBeenCalledWith('proc-1', 'gap-1'))
  })

  it('re-runs analysis when the button is clicked', async () => {
    listGapFindings.mockResolvedValue([])
    analyzeGaps.mockResolvedValue([openFinding])
    const user = userEvent.setup()
    renderPage()

    await screen.findByText(/no gaps found yet/i)
    await user.click(screen.getByRole('button', { name: /re-run analysis/i }))

    await waitFor(() => expect(analyzeGaps).toHaveBeenCalledWith('proc-1'))
    expect(await screen.findByText(/has no step before it/i)).toBeInTheDocument()
  })

  it('shows a no-open-gaps message and lets history be toggled once decided findings exist', async () => {
    const dismissed: GapFinding = { ...openFinding, status: 'dismissed' }
    listGapFindings.mockResolvedValue([dismissed])
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByText(/no open gaps/i)).toBeInTheDocument()
    expect(screen.queryByText(/dismissed/i)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /show history/i }))
    expect(screen.getByText(/^dismissed$/i)).toBeInTheDocument()
  })
})
