import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DataExportPage from './DataExportPage'
import type { TenantExport } from '../api/types'

const api = vi.hoisted(() => ({ list: vi.fn(), request: vi.fn(), download: vi.fn() }))
vi.mock('../api/client', () => ({
  ApiError: class extends Error {},
  listTenantExports: api.list,
  requestTenantExport: api.request,
  downloadTenantExport: api.download,
}))

const ready: TenantExport = {
  id: 'texp_1', status: 'ready', requested_by_name: 'Nora Admin', created_at: '2026-10-17T08:00:00',
  finished_at: '2026-10-17T08:01:00', downloaded_at: null, downloaded_by_name: null, size_bytes: 3 * 1024 * 1024,
  file_count: 12, rows: 4821, error: null, expires_at: '2026-10-20T08:01:00',
}

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue([])
})

describe('Data export page (Epic 38, US38.13)', () => {
  it('starts an export', async () => {
    api.request.mockResolvedValue({ ...ready, status: 'queued' })
    render(<DataExportPage />)
    expect(await screen.findByText('No exports yet.')).toBeInTheDocument()
    api.list.mockResolvedValue([{ ...ready, status: 'queued', rows: null }])
    fireEvent.click(screen.getByRole('button', { name: 'Export all data' }))
    await waitFor(() => expect(api.request).toHaveBeenCalled())
    expect(await screen.findByText('Waiting to start')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export in progress...' })).toBeDisabled()
  })

  it('shows what is in a ready archive and downloads it once', async () => {
    api.list.mockResolvedValue([ready])
    api.download.mockResolvedValue(new Blob(['zip']))
    URL.createObjectURL = vi.fn(() => 'blob:x')
    URL.revokeObjectURL = vi.fn()
    render(<DataExportPage />)
    const row = (await screen.findByText('Ready to download')).closest('tr')!
    expect(row).toHaveTextContent('4,821 rows, 12 files, 3.0 MB')
    api.list.mockResolvedValue([{ ...ready, status: 'downloaded', downloaded_by_name: 'Nora Admin', downloaded_at: '2026-10-17T09:00:00' }])
    fireEvent.click(screen.getByRole('button', { name: 'Download (once)' }))
    await waitFor(() => expect(api.download).toHaveBeenCalledWith('texp_1'))
    expect(await screen.findByText('Downloaded')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download (once)' })).not.toBeInTheDocument()
  })

  it('says when another export is already being made', async () => {
    api.request.mockRejectedValue(Object.assign(new Error('An export is already being made')))
    render(<DataExportPage />)
    fireEvent.click(await screen.findByRole('button', { name: 'Export all data' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not start the export')
  })
})
