import type {
  FaultConfig,
  ReviewerSetup,
  ScenarioStory,
  Situation,
  SystemBehaviour,
  TestBuilderContext,
  TwinExpectedStep,
  TwinHumanCheckpointConfig,
  TwinScenario,
  TwinScenarioCreate,
  TwinSystemStub,
} from '../api/types'

// Epic 24: everything the guided builder needs to turn a business-readable
// situation into the engine-facing scenario fields -- kept pure (no React,
// no fetch) so the mapping is unit-testable on its own.

export const SYSTEM_BEHAVIOUR_LABELS: Record<SystemBehaviour, string> = {
  normal: 'Works normally',
  down: 'Is down',
  timeout: 'Times out',
  denied: 'Refuses access',
  bad_data: 'Returns garbled data',
  no_record: 'Finds no matching record',
  custom_data: 'Returns specific data',
}

export const REVIEWER_LABELS: Record<ReviewerSetup['behaviour'], string> = {
  none: 'Not involved',
  approve: 'Always approves',
  reject: 'Always rejects',
  threshold: 'Rejects above a limit',
}

export function emptyReviewer(behaviour: ReviewerSetup['behaviour'] = 'none'): ReviewerSetup {
  return { behaviour, field: null, threshold: null, persona_name: null }
}

export function blankSituation(context: TestBuilderContext): Situation {
  return {
    story: {
      title: 'New situation',
      given: '',
      when: '',
      then: '',
      covers: [],
      source: 'manual',
      system_behaviour: {},
      system_data: {},
      reviewer: emptyReviewer(context.human_checkpoint === 'none' ? 'none' : 'approve'),
      expect: {
        systems_used: [...context.systems],
        review_decision: context.human_checkpoint === 'none' ? null : 'approve',
        output_fields: context.output_fields.map((f) => f.name),
      },
    },
    inputs: Object.fromEntries(context.input_fields.map((f) => [f.name, ''])),
  }
}

function faultFor(behaviour: SystemBehaviour): FaultConfig | null {
  switch (behaviour) {
    case 'down':
      return { type: 'dependency_unavailable', probability: 1, config: {} }
    case 'timeout':
      return { type: 'timeout', probability: 1, config: {} }
    case 'denied':
      return { type: 'permission_denied', probability: 1, config: {} }
    case 'bad_data':
      return {
        type: 'invalid_response',
        probability: 1,
        config: { response: { status: '??', payload: '#ERR!<<corrupted>>', updated: 'not-a-date' } },
      }
    case 'no_record':
      return {
        type: 'missing_data',
        probability: 1,
        config: { response: { result: null, message: 'No matching record found' } },
      }
    default:
      return null
  }
}

function checkpointConfig(reviewer: ReviewerSetup): TwinHumanCheckpointConfig {
  if (reviewer.behaviour === 'reject') {
    return { mode: 'probability', approve_probability: 0, seed: null, rule: null }
  }
  if (reviewer.behaviour === 'threshold' && reviewer.field && reviewer.threshold !== null) {
    return {
      mode: 'rule',
      approve_probability: 1,
      seed: null,
      rule: { field: reviewer.field, operator: 'gt', value: reviewer.threshold, on_true: 'reject', on_false: 'approve' },
    }
  }
  return { mode: 'probability', approve_probability: 1, seed: null, rule: null }
}

/** The expected path, in the order the engine will grade it: systems in
 * the order the user listed them, with the review either just before the
 * last system (review_before_action -- the "action" is the last call) or
 * at the end (review_after_action / escalation). */
export function expectedSteps(story: ScenarioStory, humanCheckpoint: string): TwinExpectedStep[] {
  const tools: TwinExpectedStep[] = story.expect.systems_used.map((target) => ({
    kind: 'tool_call',
    target,
    expected_decision: null,
  }))
  if (humanCheckpoint === 'none' || story.expect.review_decision === null) return tools
  const review: TwinExpectedStep = {
    kind: 'human_checkpoint',
    target: null,
    expected_decision: story.expect.review_decision,
  }
  if (humanCheckpoint === 'review_before_action' && tools.length > 0) {
    return [...tools.slice(0, -1), review, tools[tools.length - 1]]
  }
  return [...tools, review]
}

export function describeExpectedPath(story: ScenarioStory, humanCheckpoint: string): string {
  const steps = expectedSteps(story, humanCheckpoint)
  if (steps.length === 0) return 'No particular systems or reviews expected'
  return steps
    .map((step) =>
      step.kind === 'tool_call' ? `uses ${step.target}` : `reviewer ${step.expected_decision === 'approve' ? 'approves' : 'rejects'}`,
    )
    .join(' → ')
}

export function compileSituation(
  situation: Situation,
  context: TestBuilderContext,
  environmentId: string | null = null,
): TwinScenarioCreate {
  const { story } = situation
  const faults: Record<string, FaultConfig[]> = {}
  const systemStubs: Record<string, TwinSystemStub> = {}
  for (const [system, behaviour] of Object.entries(story.system_behaviour)) {
    const fault = faultFor(behaviour)
    if (fault) faults[system] = [fault]
    const data = story.system_data[system]
    if (behaviour === 'custom_data' && data) {
      systemStubs[system] = { mode: 'static', static_responses: [{ match: {}, response: data }] }
    }
  }
  return {
    name: story.title.trim() || 'Untitled situation',
    inputs: situation.inputs,
    system_stubs: systemStubs,
    human_checkpoint_config: checkpointConfig(story.reviewer),
    expected_steps: expectedSteps(story, context.human_checkpoint),
    expected_outputs: Object.fromEntries(story.expect.output_fields.map((field) => [field, null])),
    environment_id: environmentId,
    faults,
    step_match: 'in_order',
    story,
  }
}

/** Coverage target ids hit by any saved scenario's story or any draft. */
export function coveredTargetIds(scenarios: TwinScenario[], drafts: Situation[]): Set<string> {
  const covered = new Set<string>()
  for (const scenario of scenarios) scenario.story?.covers.forEach((id) => covered.add(id))
  for (const draft of drafts) draft.story.covers.forEach((id) => covered.add(id))
  return covered
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** Parses a form field back into a value of the same kind the record had
 * -- numbers stay numbers so threshold rules and grading compare like with
 * like. */
export function parseFieldValue(raw: string, previous: unknown): unknown {
  if (typeof previous === 'number') {
    const n = Number(raw)
    return raw.trim() !== '' && !Number.isNaN(n) ? n : raw
  }
  if (typeof previous === 'boolean') return raw === 'true'
  if (previous !== null && typeof previous === 'object') {
    try {
      return JSON.parse(raw)
    } catch {
      return raw
    }
  }
  return raw
}
