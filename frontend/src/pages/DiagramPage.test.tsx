import { forwardRef, useEffect, useImperativeHandle } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BPMNDocument, ProcessDetail, ProcessSummary } from '../api/types'
import DiagramPage from './DiagramPage'

const getProcess = vi.fn()
const getBpmn = vi.fn()
const listProcesses = vi.fn()
const updateBpmn = vi.fn()
const generateBpmn = vi.fn()
const finalizeProcess = vi.fn()

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  getProcess: (...args: unknown[]) => getProcess(...args),
  getBpmn: (...args: unknown[]) => getBpmn(...args),
  listProcesses: (...args: unknown[]) => listProcesses(...args),
  updateBpmn: (...args: unknown[]) => updateBpmn(...args),
  generateBpmn: (...args: unknown[]) => generateBpmn(...args),
  finalizeProcess: (...args: unknown[]) => finalizeProcess(...args),
}))

// BpmnCanvas depends on real bpmn-js/SVG layout (see BpmnCanvas.test.tsx) --
// stubbed here so DiagramPage's own logic (loading, save wiring, no-draft
// state) can be tested without a real diagram engine.
let stubShouldMarkDirty = false

vi.mock(
  '../components/BpmnCanvas',
  () => {
    const StubCanvas = forwardRef(function StubCanvas(
      props: { onDirtyChange: (dirty: boolean) => void },
      ref: React.Ref<{ exportXml(): Promise<string> }>,
    ) {
      useImperativeHandle(ref, () => ({ exportXml: async () => '<saved-xml/>' }))
      useEffect(() => {
        if (stubShouldMarkDirty) props.onDirtyChange(true)
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, [])
      return <div data-testid="bpmn-canvas-stub" />
    })
    return { default: StubCanvas }
  },
)

vi.mock('../components/ElementDetailPanel', () => ({
  default: () => <div data-testid="element-detail-panel-stub" />,
}))

vi.mock('../components/ChatPanel', () => ({
  default: () => <div data-testid="chat-panel-stub" />,
}))

const process1: ProcessDetail = {
  id: 'proc-1',
  name: 'Order Fulfillment',
  document_count: 1,
  has_draft_bpmn: true,
  finalized_version_count: 0,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  process_schema: null,
}

const bpmnDoc: BPMNDocument = {
  process_id: 'proc-1',
  xml: '<xml/>',
  low_confidence_element_ids: [],
  validation_issues: [],
  generated_at: '2026-01-01T00:00:00Z',
}

const summaries: ProcessSummary[] = [
  {
    id: 'proc-1',
    name: 'Order Fulfillment',
    document_count: 1,
    has_draft_bpmn: true,
    finalized_version_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

function renderDiagramPage() {
  render(
    <MemoryRouter initialEntries={['/processes/proc-1/diagram']}>
      <Routes>
        <Route path="/processes/:processId/diagram" element={<DiagramPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('DiagramPage', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('shows the toolbar once the process and draft BPMN load', async () => {
    getProcess.mockResolvedValue(process1)
    getBpmn.mockResolvedValue(bpmnDoc)
    listProcesses.mockResolvedValue(summaries)

    await renderDiagramPage()

    expect(await screen.findByTestId('bpmn-canvas-stub')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: /switch process/i })).toBeInTheDocument()
  })

  it('shows a no-draft message when no BPMN draft exists yet', async () => {
    getProcess.mockResolvedValue(process1)
    getBpmn.mockResolvedValue(null)
    listProcesses.mockResolvedValue(summaries)

    await renderDiagramPage()

    expect(await screen.findByText(/no draft bpmn diagram yet/i)).toBeInTheDocument()
  })

  it('disables Save until the canvas reports a change', async () => {
    getProcess.mockResolvedValue(process1)
    getBpmn.mockResolvedValue(bpmnDoc)
    listProcesses.mockResolvedValue(summaries)
    stubShouldMarkDirty = false

    await renderDiagramPage()
    await screen.findByTestId('bpmn-canvas-stub')

    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled()
  })

  it('saves the canvas-exported xml when Save is clicked', async () => {
    getProcess.mockResolvedValue(process1)
    getBpmn.mockResolvedValue(bpmnDoc)
    listProcesses.mockResolvedValue(summaries)
    updateBpmn.mockResolvedValue({ ...bpmnDoc, xml: '<saved-xml/>' })
    stubShouldMarkDirty = true

    const user = userEvent.setup()
    await renderDiagramPage()
    await screen.findByTestId('bpmn-canvas-stub')

    const saveButton = await screen.findByRole('button', { name: /^save$/i })
    await waitFor(() => expect(saveButton).toBeEnabled())
    await user.click(saveButton)

    await waitFor(() => expect(updateBpmn).toHaveBeenCalledWith('proc-1', '<saved-xml/>'))
    expect(await screen.findByText(/diagram saved/i)).toBeInTheDocument()
  })

  it('shows a link to the version history page', async () => {
    getProcess.mockResolvedValue(process1)
    getBpmn.mockResolvedValue(bpmnDoc)
    listProcesses.mockResolvedValue(summaries)
    stubShouldMarkDirty = false

    await renderDiagramPage()
    await screen.findByTestId('bpmn-canvas-stub')

    expect(screen.getByRole('link', { name: /version history/i })).toHaveAttribute(
      'href',
      '/processes/proc-1/versions',
    )
  })

  it('shows a link to the agentic blueprint page', async () => {
    getProcess.mockResolvedValue(process1)
    getBpmn.mockResolvedValue(bpmnDoc)
    listProcesses.mockResolvedValue(summaries)
    stubShouldMarkDirty = false

    await renderDiagramPage()
    await screen.findByTestId('bpmn-canvas-stub')

    expect(screen.getByRole('link', { name: /agentic blueprint/i })).toHaveAttribute(
      'href',
      '/processes/proc-1/blueprint',
    )
  })

  it('shows a link to the gap review page', async () => {
    getProcess.mockResolvedValue(process1)
    getBpmn.mockResolvedValue(bpmnDoc)
    listProcesses.mockResolvedValue(summaries)
    stubShouldMarkDirty = false

    await renderDiagramPage()
    await screen.findByTestId('bpmn-canvas-stub')

    expect(screen.getByRole('link', { name: /gap review/i })).toHaveAttribute('href', '/processes/proc-1/gaps')
  })

  it('finalizes the draft when Finalize is clicked', async () => {
    getProcess.mockResolvedValue(process1)
    getBpmn.mockResolvedValue(bpmnDoc)
    listProcesses.mockResolvedValue(summaries)
    finalizeProcess.mockResolvedValue({ id: 'ver-1', process_id: 'proc-1', label: null, created_at: '2026-01-01T00:00:00Z' })
    stubShouldMarkDirty = false
    const user = userEvent.setup()

    await renderDiagramPage()
    await screen.findByTestId('bpmn-canvas-stub')

    await user.click(screen.getByRole('button', { name: /^finalize$/i }))

    await waitFor(() => expect(finalizeProcess).toHaveBeenCalledWith('proc-1'))
    expect(await screen.findByText(/finalized as version ver-1/i)).toBeInTheDocument()
  })
})
