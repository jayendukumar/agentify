import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AgentArtifact, Situation, TestBuilderContext, TwinRun, TwinScenario } from '../api/types'
import type { AgentGroup } from '../lib/blueprintLabels'
import TwinTestBuilder from './TwinTestBuilder'

const getTestBuilderContext = vi.fn()
const listScenarios = vi.fn()
const listTwinRuns = vi.fn()
const listTwinEnvironments = vi.fn()
const suggestSituations = vi.fn()
const describeSituation = vi.fn()
const generateTestData = vi.fn()
const generateReviewerPersonas = vi.fn()
const createScenario = vi.fn()
const createTwinEnvironment = vi.fn()
const updateTwinEnvironment = vi.fn()
const startTwinRun = vi.fn()
const getTwinRun = vi.fn()
const deleteScenario = vi.fn()

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  getTestBuilderContext: (...args: unknown[]) => getTestBuilderContext(...args),
  listScenarios: (...args: unknown[]) => listScenarios(...args),
  listTwinRuns: (...args: unknown[]) => listTwinRuns(...args),
  listTwinEnvironments: (...args: unknown[]) => listTwinEnvironments(...args),
  suggestSituations: (...args: unknown[]) => suggestSituations(...args),
  describeSituation: (...args: unknown[]) => describeSituation(...args),
  generateTestData: (...args: unknown[]) => generateTestData(...args),
  generateReviewerPersonas: (...args: unknown[]) => generateReviewerPersonas(...args),
  createScenario: (...args: unknown[]) => createScenario(...args),
  createTwinEnvironment: (...args: unknown[]) => createTwinEnvironment(...args),
  updateTwinEnvironment: (...args: unknown[]) => updateTwinEnvironment(...args),
  startTwinRun: (...args: unknown[]) => startTwinRun(...args),
  getTwinRun: (...args: unknown[]) => getTwinRun(...args),
  deleteScenario: (...args: unknown[]) => deleteScenario(...args),
}))

// RunDetailView pulls in snapshot/canvas machinery that isn't under test.
vi.mock('./RunDetailView', () => ({ default: () => <div data-testid="run-detail-stub" /> }))

const artifact = {
  id: 'agent-1',
  node_ids: ['Task_check'],
  primary_node_id: 'Task_check',
  definition: {
    name: 'Invoice Check Agent',
    purpose: 'Checks incoming invoices against vendor records.',
    trigger: 'Invoice received',
    tools_systems_needed: ['Vendor DB', 'ERP'],
    human_checkpoint: 'review_before_action',
  },
} as unknown as AgentArtifact

const groups = [{ groupKey: 'Task_check', nodeIds: ['Task_check'], primary: {}, artifact }] as unknown as AgentGroup[]

const context: TestBuilderContext = {
  artifact_id: 'agent-1',
  agent_name: 'Invoice Check Agent',
  purpose: 'Checks incoming invoices',
  trigger: 'Invoice received',
  step_labels: ['Check invoice'],
  input_fields: [{ name: 'amount', source_or_destination: 'invoice', format: 'number' }],
  output_fields: [{ name: 'decision', source_or_destination: 'ERP', format: 'string' }],
  systems: ['Vendor DB', 'ERP'],
  human_checkpoint: 'review_before_action',
  success_criteria: '',
  completion_conditions: '',
  gateways: [],
  coverage_targets: [
    { id: 'happy_path', kind: 'happy_path', label: 'A typical case goes through smoothly' },
    { id: 'sys:ERP', kind: 'system_failure', label: 'ERP is unavailable or misbehaves' },
  ],
}

const suggested: Situation = {
  story: {
    title: 'ERP outage at month end',
    given: 'A $12,400 invoice from Acme arrives',
    when: 'the ERP is down',
    then: 'the agent does not post and flags it for follow-up',
    covers: ['sys:ERP'],
    source: 'suggested',
    system_behaviour: { ERP: 'down' },
    system_data: {},
    reviewer: { behaviour: 'approve', field: null, threshold: null, persona_name: null },
    expect: { systems_used: ['Vendor DB', 'ERP'], review_decision: 'approve', output_fields: ['decision'] },
  },
  inputs: { amount: 12400 },
}

function savedScenario(overrides: Partial<TwinScenario> = {}): TwinScenario {
  return {
    id: 'twinsc-1',
    agent_artifact_id: 'agent-1',
    name: suggested.story.title,
    inputs: {},
    system_stubs: {},
    human_checkpoint_config: { mode: 'probability', approve_probability: 1, seed: null, rule: null },
    expected_steps: [],
    expected_outputs: {},
    environment_id: null,
    environment_version: null,
    faults: {},
    step_match: 'in_order',
    story: suggested.story,
    created_at: '2026-10-09T00:00:00Z',
    created_by: null,
    created_by_name: null,
    ...overrides,
  }
}

function run(overrides: Partial<TwinRun> = {}): TwinRun {
  return {
    id: 'run-1',
    kind: 'twin',
    scenario_id: 'twinsc-1',
    agent_artifact_id: 'agent-1',
    status: 'COMPLETED',
    steps: [],
    final_output: {},
    deviations: [],
    graded_passed: true,
    evaluation: null,
    total_cost_usd: 0.002,
    total_tokens: 20,
    turns_used: 2,
    started_at: '2026-10-09T00:00:00Z',
    completed_at: '2026-10-09T00:00:05Z',
    run_by: null,
    run_by_name: null,
    ...overrides,
  } as TwinRun
}

describe('TwinTestBuilder', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getTestBuilderContext.mockResolvedValue(context)
    listScenarios.mockResolvedValue([])
    listTwinRuns.mockResolvedValue([])
    listTwinEnvironments.mockResolvedValue([])
  })

  it('walks from agent pick to a saved, run, story-first result', async () => {
    suggestSituations.mockResolvedValue([suggested])
    generateTestData.mockResolvedValue([{ label: 'Large Acme invoice', note: 'Near the limit', record: { amount: 9800 } }])
    createTwinEnvironment.mockResolvedValue({ id: 'env-1', name: 'Invoice Check Agent test kit', datasets: {}, personas: {}, events: [], faults: {} })
    createScenario.mockResolvedValue(savedScenario())
    startTwinRun.mockResolvedValue(run({ status: 'RUNNING', graded_passed: null }))
    getTwinRun.mockResolvedValue(run())
    const user = userEvent.setup()
    render(<TwinTestBuilder processId="proc-1" groups={groups} />)

    // 1. Pick the agent.
    await user.click(screen.getByRole('button', { name: /invoice check agent/i }))
    const coverage = await screen.findByTestId('coverage-panel')
    expect(within(coverage).getByText('0 of 2 covered')).toBeInTheDocument()

    // 2. Ask for suggestions, keep one -- coverage updates.
    await user.click(screen.getByRole('button', { name: /suggest situations/i }))
    const card = await screen.findByRole('article', { name: 'ERP outage at month end' })
    expect(within(card).getByText('the ERP is down')).toBeInTheDocument()
    expect(within(card).getByRole('list', { name: 'Conditions' })).toHaveTextContent('ERP is down')
    await user.click(within(card).getByRole('button', { name: 'Keep' }))
    expect(within(coverage).getByText('1 of 2 covered')).toBeInTheDocument()
    expect(suggestSituations).toHaveBeenCalledWith('proc-1', 'agent-1', { count: 6, focus: null, already_covered: [] })

    // 3. Set the scene: cook a typical record and use it.
    await user.click(screen.getByRole('button', { name: /next: set the scene/i }))
    const kitchen = screen.getByTestId('data-kitchen')
    await user.click(within(kitchen).getByRole('button', { name: /typical cases/i }))
    await within(kitchen).findByText('Large Acme invoice')
    await user.selectOptions(within(kitchen).getByLabelText(/use large acme invoice/i), '0')
    expect(screen.getByLabelText('amount (number)')).toHaveValue('9800')

    // 4. Expectations are pre-filled and read back as a path.
    await user.click(screen.getByRole('button', { name: /next: what should happen/i }))
    expect(screen.getByTestId('expected-path')).toHaveTextContent('uses Vendor DB → reviewer approves → uses ERP')

    // 5. Save & run.
    await user.click(screen.getByRole('button', { name: /next: review/i }))
    await user.click(screen.getByRole('button', { name: /save & run 1 situation/i }))

    await waitFor(() => expect(createScenario).toHaveBeenCalledTimes(1))
    const [, , body] = createScenario.mock.calls[0]
    expect(body).toMatchObject({
      name: 'ERP outage at month end',
      inputs: { amount: 9800 },
      step_match: 'in_order',
      environment_id: 'env-1',
      faults: { ERP: [{ type: 'dependency_unavailable', probability: 1, config: {} }] },
      expected_outputs: { decision: null },
    })
    expect(createTwinEnvironment).toHaveBeenCalledWith(
      'proc-1',
      expect.objectContaining({ name: 'Invoice Check Agent test kit', datasets: { records: expect.any(Array) } }),
    )
    expect(startTwinRun).toHaveBeenCalledWith('proc-1', 'twinsc-1')
    const results = await screen.findByTestId('suite-results')
    expect(await within(results).findByText('Behaved as expected', {}, { timeout: 4000 })).toBeInTheDocument()
    expect(within(results).getByText('1 of 1')).toBeInTheDocument()
  })

  it('turns a plain-language description into a kept situation', async () => {
    describeSituation.mockResolvedValue({ ...suggested, story: { ...suggested.story, title: 'Friday night rush', source: 'described' } })
    const user = userEvent.setup()
    render(<TwinTestBuilder processId="proc-1" groups={groups} />)

    await user.click(screen.getByRole('button', { name: /invoice check agent/i }))
    await user.type(await screen.findByLabelText('Describe a situation'), 'a big invoice on Friday night')
    await user.click(screen.getByRole('button', { name: 'Create situation' }))

    const card = await screen.findByRole('article', { name: 'Friday night rush' })
    expect(within(card).getByText('Your description')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: '✓ Kept' })).toBeInTheDocument()
    expect(describeSituation).toHaveBeenCalledWith('proc-1', 'agent-1', 'a big invoice on Friday night')
  })

  it('opens straight on results, in business language, when the agent already has a suite', async () => {
    listScenarios.mockResolvedValue([savedScenario()])
    listTwinRuns.mockResolvedValue([
      run({ graded_passed: false, deviations: [{ reason: "Expected the agent to use 'ERP', but it didn't", step_index: null }] }),
    ])
    const user = userEvent.setup()
    render(<TwinTestBuilder processId="proc-1" groups={groups} />)

    await user.click(screen.getByRole('button', { name: /invoice check agent/i }))
    const results = await screen.findByTestId('suite-results')
    expect(within(results).getByText("Didn't behave as expected")).toBeInTheDocument()
    expect(within(results).getByText("Expected the agent to use 'ERP', but it didn't")).toBeInTheDocument()
    expect(within(results).getByText('0 of 1')).toBeInTheDocument()
  })

  it('warns on the agent card when the release gate would refuse a run', () => {
    const notReady = {
      ...artifact,
      checklist: [
        { key: 'permissions', label: 'Permissions', satisfied: false, detail: '' },
        { key: 'test_scenarios', label: 'Test scenarios', satisfied: false, detail: '' },
      ],
    } as unknown as AgentArtifact
    render(<TwinTestBuilder processId="proc-1" groups={[{ ...groups[0], artifact: notReady }]} />)
    expect(screen.getByText(/needs setup before it can run: permissions\. regenerate/i)).toBeInTheDocument()
  })

  it('explains what to do when there are no agents yet', () => {
    render(<TwinTestBuilder processId="proc-1" groups={[]} />)
    expect(screen.getByText(/generate an agent from the agents tab first/i)).toBeInTheDocument()
  })
})
