import { forwardRef, useEffect } from 'react'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentArtifact, BlueprintOverlay, ProcessDetail, TwinRun, TwinScenario, VersionDetail } from '../api/types'
import BlueprintPage from './BlueprintPage'

const getProcess = vi.fn()
const getBlueprint = vi.fn()
const getVersion = vi.fn()
const generateBlueprint = vi.fn()
const overrideBlueprintNode = vi.fn()
const exportBlueprint = vi.fn()
const generateAgentArtifact = vi.fn()
const listAgentArtifacts = vi.fn()
const listRegistries = vi.fn()
const pushToRegistry = vi.fn()
const getPublishStatus = vi.fn()
const publishAgentArtifact = vi.fn()
const markPublicationDeployed = vi.fn()
const listScenarios = vi.fn()
const createScenario = vi.fn()
const deleteScenario = vi.fn()
const runScenario = vi.fn()
const listTwinRuns = vi.fn()
const getTwinSummary = vi.fn()
const setTwinBaseline = vi.fn()
const deleteTwinBaseline = vi.fn()

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
  listRegistries: (...args: unknown[]) => listRegistries(...args),
  pushToRegistry: (...args: unknown[]) => pushToRegistry(...args),
  getPublishStatus: (...args: unknown[]) => getPublishStatus(...args),
  publishAgentArtifact: (...args: unknown[]) => publishAgentArtifact(...args),
  markPublicationDeployed: (...args: unknown[]) => markPublicationDeployed(...args),
  listScenarios: (...args: unknown[]) => listScenarios(...args),
  createScenario: (...args: unknown[]) => createScenario(...args),
  deleteScenario: (...args: unknown[]) => deleteScenario(...args),
  runScenario: (...args: unknown[]) => runScenario(...args),
  listTwinRuns: (...args: unknown[]) => listTwinRuns(...args),
  getTwinSummary: (...args: unknown[]) => getTwinSummary(...args),
  setTwinBaseline: (...args: unknown[]) => setTwinBaseline(...args),
  deleteTwinBaseline: (...args: unknown[]) => deleteTwinBaseline(...args),
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
    listRegistries.mockResolvedValue([])
    getPublishStatus.mockResolvedValue({
      agent_artifact_id: 'agent-1',
      lifecycle_status: 'generated',
      needs_republish: false,
      latest_publication: null,
      publications: [],
    })
    getTwinSummary.mockResolvedValue({
      agent_artifact_id: 'agent-1',
      run_count: 0,
      pass_rate: null,
      total_cost_usd: null,
      average_cost_usd: null,
      common_failure_reasons: [],
      baseline: null,
      baseline_comparison: null,
    })
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

  it('shows a progress indicator while a blueprint is generating', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(null)
    let resolveGenerate!: (value: typeof overlay) => void
    generateBlueprint.mockImplementation(() => new Promise((resolve) => { resolveGenerate = resolve }))
    getVersion.mockResolvedValue(versionDetail)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /generate blueprint/i }))

    expect(await screen.findByRole('progressbar', { name: /blueprint generation progress/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generating...' })).toBeDisabled()

    await act(async () => resolveGenerate(overlay))

    await waitFor(() => expect(screen.queryByRole('progressbar')).not.toBeInTheDocument())
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

  it('shows twin confidence on the Blueprint detail panel (US14.6)', async () => {
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
      generated_by: null,
      generated_by_name: null,
    }
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    listAgentArtifacts.mockResolvedValue([artifact])
    getTwinSummary.mockResolvedValue({
      agent_artifact_id: 'agent-1',
      run_count: 4,
      pass_rate: 0.75,
      total_cost_usd: 0.01,
      average_cost_usd: 0.0025,
      common_failure_reasons: ['Step 0: expected tool_call...'],
      baseline: null,
      baseline_comparison: null,
    })
    stubSelectedId = 'Task_a'
    renderPage()

    await screen.findByText('Request Reviewer Agent')
    const badge = await screen.findByTestId('twin-confidence-badge')
    expect(within(badge).getByText('75% pass')).toBeInTheDocument()
    expect(within(badge).getByText('(4 runs)')).toBeInTheDocument()
  })

  it('publishes an agent artifact to a registry from the Blueprint detail panel (Epic 15)', async () => {
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
      generated_by: null,
      generated_by_name: null,
    }
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    listAgentArtifacts.mockResolvedValue([artifact])
    listRegistries.mockResolvedValue([{ name: 'local', type: 'local', reachable: true, authenticated: true, message: null }])
    const publication = {
      id: 'pub-1',
      agent_artifact_id: 'agent-1',
      registry_name: 'local',
      registry_entry_id: 'regentry-1',
      version: 1,
      status: 'published',
      published_at: '2026-01-05T00:00:00Z',
      published_by: 'user-1',
      published_by_name: 'Alice',
      deployed_at: null,
      deployed_by: null,
      deployed_by_name: null,
    }
    publishAgentArtifact.mockResolvedValue(publication)
    getPublishStatus
      .mockResolvedValueOnce({
        agent_artifact_id: 'agent-1',
        lifecycle_status: 'generated',
        needs_republish: false,
        latest_publication: null,
        publications: [],
      })
      .mockResolvedValue({
        agent_artifact_id: 'agent-1',
        lifecycle_status: 'published',
        needs_republish: false,
        latest_publication: publication,
        publications: [publication],
      })
    stubSelectedId = 'Task_a'
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Request Reviewer Agent')
    expect(await screen.findByText(/not published/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /^publish$/i }))

    await waitFor(() => expect(publishAgentArtifact).toHaveBeenCalledWith('proc-1', 'agent-1', 'local'))
    expect(await screen.findByText(/pushed to .local./i)).toBeInTheDocument()
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

  it('shows an agent card on the Agents tab and its detail on click', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    const user = userEvent.setup()
    renderPage()

    await screen.findByTestId('blueprint-canvas-stub')
    await user.click(screen.getByRole('tab', { name: /agents/i }))

    const agentsPanel = within(screen.getByTestId('agent-cards-panel'))
    await user.click(agentsPanel.getByRole('button', { name: /request reviewer agent/i }))

    expect(agentsPanel.getByText('What is this agent?')).toBeInTheDocument()
    expect(agentsPanel.getByText('How will it work?')).toBeInTheDocument()
    expect(agentsPanel.getByText('What tools might it require?')).toBeInTheDocument()
    expect(agentsPanel.getByText('Why was this agent selected?')).toBeInTheDocument()
    expect(agentsPanel.getByText('What governance would it need?')).toBeInTheDocument()
    expect(agentsPanel.getByText('Intake system')).toBeInTheDocument()
    expect(agentsPanel.getByText(/looks up known data\./i)).toBeInTheDocument()
  })

  it('lists and runs a scenario on the Digital Twin Simulation tab', async () => {
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
        tools_systems_needed: ['Intake system'],
        human_checkpoint: 'none',
        model: 'test-model',
      },
      baseline_version_id: 'ver-1',
      generated_at: '2026-01-03T00:00:00Z',
      generated_by: null,
      generated_by_name: null,
    }
    const scenario: TwinScenario = {
      id: 'twinsc-1',
      agent_artifact_id: 'agent-1',
      name: 'Happy path',
      inputs: {},
      system_stubs: {},
      human_checkpoint_config: { mode: 'probability', approve_probability: 1, seed: null, rule: null },
      expected_steps: [],
      expected_outputs: {},
      created_at: '2026-01-03T00:00:00Z',
      created_by: null,
      created_by_name: null,
    }
    const run: TwinRun = {
      id: 'twinrun-1',
      scenario_id: 'twinsc-1',
      agent_artifact_id: 'agent-1',
      status: 'passed',
      trace: [],
      final_output: {},
      deviations: [],
      total_cost_usd: 0.001,
      total_tokens: 10,
      turns_used: 1,
      started_at: '2026-01-03T00:00:00Z',
      completed_at: '2026-01-03T00:00:01Z',
      run_by: null,
      run_by_name: null,
    }

    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    listAgentArtifacts.mockResolvedValue([artifact])
    listScenarios.mockResolvedValue([scenario])
    listTwinRuns.mockResolvedValue([])
    getTwinSummary.mockResolvedValue({
      agent_artifact_id: 'agent-1',
      run_count: 0,
      pass_rate: null,
      total_cost_usd: null,
      average_cost_usd: null,
      common_failure_reasons: [],
      baseline: null,
      baseline_comparison: null,
    })
    runScenario.mockResolvedValue(run)
    const user = userEvent.setup()
    renderPage()

    await screen.findByTestId('blueprint-canvas-stub')
    await user.click(screen.getByRole('tab', { name: /digital twin simulation/i }))

    const twinPanel = within(await screen.findByTestId('digital-twin-panel'))
    expect(await twinPanel.findByText('Happy path')).toBeInTheDocument()

    await user.click(twinPanel.getByRole('button', { name: /^run$/i }))

    await waitFor(() => expect(runScenario).toHaveBeenCalledWith('proc-1', 'twinsc-1'))
    expect(await twinPanel.findByText('passed')).toBeInTheDocument()
  })

  it('saves a manual baseline on the Digital Twin Simulation tab (US14.5)', async () => {
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
      generated_by: null,
      generated_by_name: null,
    }
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    listAgentArtifacts.mockResolvedValue([artifact])
    listScenarios.mockResolvedValue([])
    listTwinRuns.mockResolvedValue([])
    getTwinSummary.mockResolvedValue({
      agent_artifact_id: 'agent-1',
      run_count: 0,
      pass_rate: null,
      total_cost_usd: null,
      average_cost_usd: null,
      common_failure_reasons: [],
      baseline: null,
      baseline_comparison: null,
    })
    setTwinBaseline.mockResolvedValue({
      agent_artifact_id: 'agent-1',
      typical_time_seconds: 300,
      error_rate: 0.2,
      notes: null,
      recorded_at: '2026-01-04T00:00:00Z',
      recorded_by: null,
      recorded_by_name: null,
    })
    const user = userEvent.setup()
    renderPage()

    await screen.findByTestId('blueprint-canvas-stub')
    await user.click(screen.getByRole('tab', { name: /digital twin simulation/i }))

    const twinPanel = within(await screen.findByTestId('digital-twin-panel'))
    await user.type(twinPanel.getByLabelText(/typical time to complete/i), '300')
    await user.type(twinPanel.getByLabelText(/error rate/i), '20')
    await user.click(twinPanel.getByRole('button', { name: /save baseline/i }))

    await waitFor(() =>
      expect(setTwinBaseline).toHaveBeenCalledWith('proc-1', 'agent-1', {
        typical_time_seconds: 300,
        error_rate: 0.2,
        notes: null,
      }),
    )
  })

  it('shows the connected-agent chain on the Digital Twin Preview tab', async () => {
    getProcess.mockResolvedValue(process1)
    getBlueprint.mockResolvedValue(overlay)
    getVersion.mockResolvedValue(versionDetail)
    const user = userEvent.setup()
    renderPage()

    await screen.findByTestId('blueprint-canvas-stub')
    await user.click(screen.getByRole('tab', { name: /digital twin preview/i }))

    const twinPanel = within(screen.getByTestId('digital-twin-preview'))
    expect(twinPanel.getByText(/hypothetical preview, not a verified simulation/i)).toBeInTheDocument()
    expect(twinPanel.getByText('Request Reviewer Agent')).toBeInTheDocument()
    expect(twinPanel.getByText('Send confirmation')).toBeInTheDocument()
    expect(twinPanel.getByText(/no gaps identified/i)).toBeInTheDocument()
  })
})
