import { forwardRef, useEffect } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentArtifact, BlueprintOverlay, ProcessDetail, VersionDetail } from '../api/types'
import BlueprintPage from './BlueprintPage'

const getProcess = vi.fn()
const getBlueprint = vi.fn()
const getVersion = vi.fn()
const generateBlueprint = vi.fn()
const overrideBlueprintNode = vi.fn()
const exportBlueprint = vi.fn()
const generateAgentArtifact = vi.fn()
const listAgentArtifacts = vi.fn()

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  getProcess: (...args: unknown[]) => getProcess(...args),
  getBlueprint: (...args: unknown[]) => getBlueprint(...args),
  getVersion: (...args: unknown[]) => getVersion(...args),
  generateBlueprint: (...args: unknown[]) => generateBlueprint(...args),
  overrideBlueprintNode: (...args: unknown[]) => overrideBlueprintNode(...args),
  exportBlueprint: (...args: unknown[]) => exportBlueprint(...args),
  generateAgentArtifact: (...args: unknown[]) => generateAgentArtifact(...args),
  listAgentArtifacts: (...args: unknown[]) => listAgentArtifacts(...args),
}))

// BlueprintCanvas depends on real bpmn-js/SVG layout -- stubbed here so
// BlueprintPage's own logic (loading, summary stats, generate/override
// wiring) can be tested without a real diagram engine, same convention as
// DiagramPage.test.tsx's BpmnCanvas stub.
let stubSelectedId: string | null = null

vi.mock('../components/BlueprintCanvas', () => {
  const StubCanvas = forwardRef(function StubCanvas(props: {
    onSelectionChange: (id: string | null) => void
    onDiagramReady: (labels: Record<string, string>) => void
  }) {
    useEffect(() => {
      props.onDiagramReady({ Task_a: 'Review request', Task_b: 'Send confirmation' })
      if (stubSelectedId) props.onSelectionChange(stubSelectedId)
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])
    return <div data-testid="blueprint-canvas-stub" />
  })
  return { default: StubCanvas }
})

const process1: ProcessDetail = {
  id: 'proc-1',
  name: 'Order Fulfillment',
  document_count: 1,
  has_draft_bpmn: true,
  finalized_version_count: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  process_schema: null,
}

const overlay: BlueprintOverlay = {
  process_id: 'proc-1',
  baseline_version_id: 'ver-1',
  generated_at: '2026-01-02T00:00:00Z',
  nodes: [
    {
      node_id: 'Task_a',
      verdict: 'automatable',
      step_type: 'data_retrieval_transformation',
      rationale: 'Looks up known data.',
      agent_spec: {
        name: 'Request Reviewer Agent',
        purpose: 'Reviews incoming requests against policy.',
        trigger: 'New request submitted',
        required_inputs: [{ name: 'Request form', source_or_destination: 'Intake system', format: 'JSON' }],
        expected_outputs: [{ name: 'Review result', source_or_destination: 'Case system', format: 'JSON' }],
        tools_systems_needed: ['Intake system'],
        human_checkpoint: 'none',
        consolidated_from_nodes: [],
      },
      not_automatable_reason: null,
      overridden: false,
      override_justification: null,
      overridden_by: null,
      overridden_by_name: null,
    },
    {
      node_id: 'Task_b',
      verdict: 'not_automatable',
      step_type: 'approval_compliance_signoff',
      rationale: 'Requires accountable sign-off.',
      agent_spec: null,
      not_automatable_reason: 'Requires legal authority to approve.',
      overridden: false,
      override_justification: null,
      overridden_by: null,
      overridden_by_name: null,
    },
  ],
}

const versionDetail: VersionDetail = {
  id: 'ver-1',
  process_id: 'proc-1',
  label: null,
  created_at: '2026-01-01T00:00:00Z',
  created_by: null,
  created_by_name: null,
  xml: '<xml/>',
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/processes/proc-1/blueprint']}>
      <Routes>
        <Route path="/processes/:processId/blueprint" element={<BlueprintPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('BlueprintPage', () => {
  beforeEach(() => {
    listAgentArtifacts.mockResolvedValue([])
  })

  afterEach(() => {
    vi.clearAllMocks()
    stubSelectedId = null
  })

  it('offers to generate a blueprint when none exists yet', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(null)
    renderPage()

    expect(await screen.findByText(/no blueprint generated yet/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /generate blueprint/i })).toBeInTheDocument()
  })

  it('prompts to finalize first when there is no finalized version', async () => {
    getProcess.mockResolvedValue({ ...process1, finalized_version_count: 0 })
    getBlueprint.mockResolvedValue(null)
    renderPage()

    expect(await screen.findByText(/finalize a diagram baseline first/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /generate blueprint/i })).not.toBeInTheDocument()
  })

  it('generates a blueprint on demand', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(null)
    generateBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /generate blueprint/i }))

    await waitFor(() => expect(generateBlueprint).toHaveBeenCalledWith('proc-1'))
    expect(await screen.findByTestId('blueprint-canvas-stub')).toBeInTheDocument()
  })

  it('shows the summary dashboard once a blueprint exists', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    renderPage()

    await screen.findByTestId('blueprint-canvas-stub')
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText(/of steps automatable or partially automatable/i)).toBeInTheDocument()
    expect(screen.getByText(/1 automatable, 0 partial, 1 not automatable/i)).toBeInTheDocument()

    await userEvent.setup().click(screen.getByText(/not automatable steps/i))
    expect(screen.getByText(/requires legal authority to approve/i)).toBeInTheDocument()
  })

  it('shows agent spec details for a selected automatable node', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    stubSelectedId = 'Task_a'
    renderPage()

    expect(await screen.findByText('Request Reviewer Agent')).toBeInTheDocument()
    expect(screen.getByText(/reviews incoming requests against policy/i)).toBeInTheDocument()
  })

  it('generates an agent artifact for the selected node', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    const artifact: AgentArtifact = {
      id: 'agent-1',
      process_id: 'proc-1',
      group_key: 'Task_a',
      node_ids: ['Task_a'],
      primary_node_id: 'Task_a',
      status: 'generated',
      definition: {
        name: 'Request Reviewer Agent',
        purpose: 'Reviews incoming requests against policy.',
        trigger: 'New request submitted',
        system_prompt: 'You are Request Reviewer Agent...',
        input_schema: [],
        output_schema: [],
        tools_systems_needed: [],
        human_checkpoint: 'none',
        model: 'test-model',
      },
      baseline_version_id: 'ver-1',
      generated_at: '2026-01-03T00:00:00Z',
      generated_by: 'user-1',
      generated_by_name: 'Alice',
    }
    generateAgentArtifact.mockResolvedValue(artifact)
    listAgentArtifacts.mockResolvedValueOnce([]).mockResolvedValueOnce([artifact])
    stubSelectedId = 'Task_a'
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Request Reviewer Agent')
    await user.click(screen.getByRole('button', { name: /^generate agent$/i }))

    await waitFor(() => expect(generateAgentArtifact).toHaveBeenCalledWith('proc-1', 'Task_a'))
    expect(await screen.findByText('generated')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /regenerate agent/i })).toBeInTheDocument()
  })

  it('shows a stale badge for an artifact whose blueprint node changed', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    listAgentArtifacts.mockResolvedValue([
      {
        id: 'agent-1',
        process_id: 'proc-1',
        group_key: 'Task_a',
        node_ids: ['Task_a'],
        primary_node_id: 'Task_a',
        status: 'stale',
        definition: {
          name: 'Request Reviewer Agent',
          purpose: 'Reviews incoming requests against policy.',
          trigger: 'New request submitted',
          system_prompt: 'You are Request Reviewer Agent...',
          input_schema: [],
          output_schema: [],
          tools_systems_needed: [],
          human_checkpoint: 'none',
          model: 'test-model',
        },
        baseline_version_id: 'ver-1',
        generated_at: '2026-01-03T00:00:00Z',
        generated_by: null,
        generated_by_name: null,
      } satisfies AgentArtifact,
    ])
    stubSelectedId = 'Task_a'
    renderPage()

    expect(await screen.findByText(/stale -- regenerate/i)).toBeInTheDocument()
  })

  it('submits an override with a justification', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    overrideBlueprintNode.mockResolvedValue({
      ...overlay,
      nodes: [
        { ...overlay.nodes[0], verdict: 'not_automatable', overridden: true, override_justification: 'Needs a human.' },
        overlay.nodes[1],
      ],
    })
    stubSelectedId = 'Task_a'
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Request Reviewer Agent')
    await user.click(screen.getByRole('button', { name: /override assessment/i }))
    await user.selectOptions(screen.getByLabelText(/corrected verdict/i), 'not_automatable')
    await user.type(screen.getByLabelText(/justification/i), 'Needs a human.')
    await user.click(screen.getByRole('button', { name: /save override/i }))

    await waitFor(() =>
      expect(overrideBlueprintNode).toHaveBeenCalledWith('proc-1', 'Task_a', 'not_automatable', 'Needs a human.'),
    )
    expect(await screen.findByText(/needs a human\./i)).toBeInTheDocument()
  })

  it('exports the blueprint as markdown', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    exportBlueprint.mockResolvedValue('# Blueprint')
    const user = userEvent.setup()
    renderPage()

    await screen.findByTestId('blueprint-canvas-stub')
    await user.click(screen.getByRole('button', { name: /export markdown/i }))

    await waitFor(() => expect(exportBlueprint).toHaveBeenCalledWith('proc-1'))
  })
})
