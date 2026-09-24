import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ProcessSummary } from '../api/types'
import ProcessListPage from './ProcessListPage'

const listProcesses = vi.fn()
const createProcess = vi.fn()
const deleteProcess = vi.fn()
vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  listProcesses: (...args: unknown[]) => listProcesses(...args),
  createProcess: (...args: unknown[]) => createProcess(...args),
  deleteProcess: (...args: unknown[]) => deleteProcess(...args),
}))
afterEach(() => vi.resetAllMocks())

describe('Process workspace feedback', () => {
  it('allows recovery from a loading failure without showing an empty success state', async () => {
    listProcesses.mockRejectedValueOnce(new Error('offline')).mockResolvedValue([])
    const user = userEvent.setup()
    render(<MemoryRouter><ProcessListPage /></MemoryRouter>)
    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to load processes')
    expect(screen.queryByText(/no processes yet/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(/no processes yet/i)).toBeInTheDocument()
  })

  it('prevents duplicate creation while pending and announces success', async () => {
    listProcesses.mockResolvedValue([])
    let complete!: () => void
    createProcess.mockImplementation(() => new Promise<void>(resolve => { complete = resolve }))
    const user = userEvent.setup()
    render(<MemoryRouter><ProcessListPage /></MemoryRouter>)
    await screen.findByText(/no processes yet/i)
    await user.type(screen.getByRole('textbox', { name: 'Process name' }), 'Invoice approval')
    await user.click(screen.getByRole('button', { name: 'Create process' }))
    expect(screen.getByRole('button', { name: 'Creating...' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Process name' })).toBeDisabled()
    expect(createProcess).toHaveBeenCalledTimes(1)
    await act(async () => complete())
    expect(await screen.findByRole('status')).toHaveTextContent('Invoice approval')
  })
})

describe('Deleting a process', () => {
  const process: ProcessSummary = {
    id: 'proc-1',
    name: 'Invoice approval',
    document_count: 2,
    has_draft_bpmn: false,
    finalized_version_count: 0,
    created_at: '',
    updated_at: '',
  }

  it('deletes the process on confirm and refreshes the list', async () => {
    listProcesses.mockResolvedValue([process])
    deleteProcess.mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const user = userEvent.setup()
    render(<MemoryRouter><ProcessListPage /></MemoryRouter>)

    await screen.findByText('Invoice approval')
    listProcesses.mockResolvedValue([])
    await user.click(screen.getByRole('button', { name: 'Delete' }))

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('Invoice approval'))
    expect(deleteProcess).toHaveBeenCalledWith('proc-1')
    await waitFor(() => expect(screen.queryByText('Invoice approval')).not.toBeInTheDocument())
  })

  it('does not delete when the confirmation is declined', async () => {
    listProcesses.mockResolvedValue([process])
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    const user = userEvent.setup()
    render(<MemoryRouter><ProcessListPage /></MemoryRouter>)

    await user.click(await screen.findByRole('button', { name: 'Delete' }))

    expect(deleteProcess).not.toHaveBeenCalled()
    expect(screen.getByText('Invoice approval')).toBeInTheDocument()
  })
})
