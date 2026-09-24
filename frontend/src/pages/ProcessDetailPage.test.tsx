import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { DocumentSummary, ProcessDetail } from '../api/types'
import ProcessDetailPage from './ProcessDetailPage'

const getProcess = vi.fn()
const listDocuments = vi.fn()
const getBlueprint = vi.fn()
const listGapFindings = vi.fn()
const uploadDocuments = vi.fn()
const generateBpmn = vi.fn()
const deleteDocument = vi.fn()

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  getProcess: (...args: unknown[]) => getProcess(...args),
  listDocuments: (...args: unknown[]) => listDocuments(...args),
  getBlueprint: (...args: unknown[]) => getBlueprint(...args),
  listGapFindings: (...args: unknown[]) => listGapFindings(...args),
  uploadDocuments: (...args: unknown[]) => uploadDocuments(...args),
  generateBpmn: (...args: unknown[]) => generateBpmn(...args),
  deleteDocument: (...args: unknown[]) => deleteDocument(...args),
}))

afterEach(() => vi.resetAllMocks())

const process: ProcessDetail = {
  id: 'proc-1',
  name: 'Onboarding',
  document_count: 1,
  has_draft_bpmn: false,
  finalized_version_count: 0,
  created_at: '',
  updated_at: '',
  process_schema: null,
}

const failedDoc: DocumentSummary = {
  id: 'doc-failed',
  process_id: 'proc-1',
  filename: 'backlog.docx',
  content_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  size_bytes: 2048,
  status: 'failed',
  process_definition_confidence: 0,
  validation_message: 'Not a process document.',
  created_at: '',
}

const doneDoc: DocumentSummary = {
  ...failedDoc,
  id: 'doc-done',
  filename: 'sop.docx',
  status: 'done',
  process_definition_confidence: 92,
  validation_message: 'Ordered steps.',
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/processes/proc-1']}>
      <Routes>
        <Route path="/processes/:processId" element={<ProcessDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Deleting a failed document', () => {
  it('offers delete only for failed documents, and removes it on confirm', async () => {
    getProcess.mockResolvedValue(process)
    listDocuments.mockResolvedValue([failedDoc, doneDoc])
    getBlueprint.mockResolvedValue(null)
    listGapFindings.mockResolvedValue([])
    deleteDocument.mockResolvedValue(undefined)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const user = userEvent.setup()
    renderPage()

    await screen.findByText('backlog.docx')
    const deleteButtons = screen.getAllByRole('button', { name: 'Delete' })
    expect(deleteButtons).toHaveLength(1) // only the failed document gets one

    listDocuments.mockResolvedValue([doneDoc])
    await user.click(deleteButtons[0])

    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('backlog.docx'))
    expect(deleteDocument).toHaveBeenCalledWith('proc-1', 'doc-failed')
    await waitFor(() => expect(screen.queryByText('backlog.docx')).not.toBeInTheDocument())
  })

  it('does not delete when the confirmation is declined', async () => {
    getProcess.mockResolvedValue(process)
    listDocuments.mockResolvedValue([failedDoc])
    getBlueprint.mockResolvedValue(null)
    listGapFindings.mockResolvedValue([])
    vi.spyOn(window, 'confirm').mockReturnValue(false)

    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Delete' }))

    expect(deleteDocument).not.toHaveBeenCalled()
    expect(screen.getByText('backlog.docx')).toBeInTheDocument()
  })

  it('shows an error and keeps the document listed if deletion fails', async () => {
    getProcess.mockResolvedValue(process)
    listDocuments.mockResolvedValue([failedDoc])
    getBlueprint.mockResolvedValue(null)
    listGapFindings.mockResolvedValue([])
    const { ApiError } = await import('../api/client')
    deleteDocument.mockRejectedValue(new ApiError(400, 'Only a failed document can be deleted.'))
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Delete' }))

    expect(await screen.findByText('Only a failed document can be deleted.')).toBeInTheDocument()
    expect(screen.getByText('backlog.docx')).toBeInTheDocument()
  })
})
