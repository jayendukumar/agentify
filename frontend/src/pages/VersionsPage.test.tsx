import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { VersionDiffResult, VersionSummary } from '../api/types'
import VersionsPage from './VersionsPage'

const listVersions = vi.fn()
const restoreVersion = vi.fn()
const diffVersions = vi.fn()

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  listVersions: (...args: unknown[]) => listVersions(...args),
  restoreVersion: (...args: unknown[]) => restoreVersion(...args),
  diffVersions: (...args: unknown[]) => diffVersions(...args),
}))

const v1: VersionSummary = { id: 'ver-1', process_id: 'proc-1', label: null, created_at: '2026-01-01T00:00:00Z' }
const v2: VersionSummary = { id: 'ver-2', process_id: 'proc-1', label: null, created_at: '2026-01-02T00:00:00Z' }

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/processes/proc-1/versions']}>
      <Routes>
        <Route path="/processes/:processId/versions" element={<VersionsPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('VersionsPage', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows an empty state when there are no versions yet', async () => {
    listVersions.mockResolvedValue([])
    renderPage()

    expect(await screen.findByText(/no finalized versions yet/i)).toBeInTheDocument()
  })

  it('lists versions with a fallback label', async () => {
    listVersions.mockResolvedValue([v1, v2])
    renderPage()

    const list = await screen.findByRole('list')
    expect(within(list).getByText('Version 1')).toBeInTheDocument()
    expect(within(list).getByText('Version 2')).toBeInTheDocument()
  })

  it('restores a version after a two-step confirm', async () => {
    listVersions.mockResolvedValue([v1, v2])
    restoreVersion.mockResolvedValue(v1)
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('list')
    const restoreButtons = screen.getAllByRole('button', { name: /^restore$/i })
    await user.click(restoreButtons[0])

    expect(restoreVersion).not.toHaveBeenCalled()
    const confirmButton = screen.getByRole('button', { name: /confirm restore/i })
    await user.click(confirmButton)

    await waitFor(() => expect(restoreVersion).toHaveBeenCalledWith('proc-1', 'ver-1'))
    expect(await screen.findByText(/version restored/i)).toBeInTheDocument()
  })

  it('compares two versions and renders labeled added/removed/changed lists', async () => {
    listVersions.mockResolvedValue([v1, v2])
    const diffResult: VersionDiffResult = {
      from_version_id: 'ver-1',
      to_version_id: 'ver-2',
      added_element_ids: ['Task_c'],
      removed_element_ids: ['Task_b'],
      changed_element_ids: ['Task_a'],
      labels: { Task_c: 'Step C', Task_b: 'Step B', Task_a: 'Step A Renamed' },
    }
    diffVersions.mockResolvedValue(diffResult)
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('list')
    await user.click(screen.getByRole('button', { name: /^compare$/i }))

    await waitFor(() => expect(diffVersions).toHaveBeenCalledWith('proc-1', 'ver-1', 'ver-2'))
    expect(await screen.findByText('Step C')).toBeInTheDocument()
    expect(screen.getByText('Step B')).toBeInTheDocument()
    expect(screen.getByText('Step A Renamed')).toBeInTheDocument()
  })
})
