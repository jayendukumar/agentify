import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ProcessListPage from './ProcessListPage'

const listProcesses = vi.fn()
const createProcess = vi.fn()
vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {},
  listProcesses: (...args: unknown[]) => listProcesses(...args),
  createProcess: (...args: unknown[]) => createProcess(...args),
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
