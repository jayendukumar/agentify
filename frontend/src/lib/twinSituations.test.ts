import { describe, expect, it } from 'vitest'
import type { Situation, TestBuilderContext, TwinScenario } from '../api/types'
import {
  blankSituation,
  compileSituation,
  coveredTargetIds,
  describeExpectedPath,
  emptyReviewer,
  expectedSteps,
  parseFieldValue,
} from './twinSituations'

const context: TestBuilderContext = {
  artifact_id: 'agent-1',
  agent_name: 'Invoice Agent',
  purpose: 'Check invoices',
  trigger: 'Invoice received',
  step_labels: ['Check invoice'],
  input_fields: [{ name: 'amount', source_or_destination: 'invoice', format: 'number' }],
  output_fields: [{ name: 'decision', source_or_destination: 'ERP', format: 'string' }],
  systems: ['Vendor DB', 'ERP'],
  human_checkpoint: 'review_before_action',
  success_criteria: '',
  completion_conditions: '',
  gateways: [],
  coverage_targets: [],
}

function situation(overrides: Partial<Situation['story']> = {}, inputs: Record<string, unknown> = { amount: 120 }): Situation {
  const base = blankSituation(context)
  return { story: { ...base.story, title: 'Big invoice', ...overrides }, inputs }
}

describe('compileSituation', () => {
  it('maps system behaviours to scenario faults and static stubs', () => {
    const compiled = compileSituation(
      situation({
        system_behaviour: { ERP: 'down', 'Vendor DB': 'custom_data' },
        system_data: { 'Vendor DB': { vendor: 'Acme', status: 'blocked' } },
      }),
      context,
    )
    expect(compiled.faults).toEqual({ ERP: [{ type: 'dependency_unavailable', probability: 1, config: {} }] })
    expect(compiled.system_stubs).toEqual({
      'Vendor DB': { mode: 'static', static_responses: [{ match: {}, response: { vendor: 'Acme', status: 'blocked' } }] },
    })
    expect(compiled.step_match).toBe('in_order')
    expect(compiled.inputs).toEqual({ amount: 120 })
    expect(compiled.story?.title).toBe('Big invoice')
  })

  it('compiles a threshold reviewer into a rule', () => {
    const compiled = compileSituation(
      situation({ reviewer: { ...emptyReviewer('threshold'), field: 'amount', threshold: 5000 } }),
      context,
    )
    expect(compiled.human_checkpoint_config).toEqual({
      mode: 'rule',
      approve_probability: 1,
      seed: null,
      rule: { field: 'amount', operator: 'gt', value: 5000, on_true: 'reject', on_false: 'approve' },
    })
  })

  it('compiles an always-reject reviewer to probability 0', () => {
    const compiled = compileSituation(situation({ reviewer: emptyReviewer('reject') }), context)
    expect(compiled.human_checkpoint_config?.approve_probability).toBe(0)
  })

  it('turns expected output fields into presence checks', () => {
    expect(compileSituation(situation(), context).expected_outputs).toEqual({ decision: null })
  })
})

describe('expectedSteps', () => {
  it('places a before-action review just before the last system', () => {
    const steps = expectedSteps(situation().story, 'review_before_action')
    expect(steps.map((s) => s.target ?? s.expected_decision)).toEqual(['Vendor DB', 'approve', 'ERP'])
  })

  it('places an after-action review at the end', () => {
    const steps = expectedSteps(situation().story, 'review_after_action')
    expect(steps.map((s) => s.target ?? s.expected_decision)).toEqual(['Vendor DB', 'ERP', 'approve'])
  })

  it('omits the review when none is expected or the agent has no checkpoint', () => {
    expect(expectedSteps(situation().story, 'none')).toHaveLength(2)
    const noReview = situation({ expect: { systems_used: ['ERP'], review_decision: null, output_fields: [] } })
    expect(expectedSteps(noReview.story, 'review_before_action')).toHaveLength(1)
  })

  it('reads as a plain-language path', () => {
    expect(describeExpectedPath(situation().story, 'review_before_action')).toBe(
      'uses Vendor DB → reviewer approves → uses ERP',
    )
  })
})

describe('coveredTargetIds', () => {
  it('unions saved scenario stories and drafts', () => {
    const saved = { story: { ...situation().story, covers: ['happy_path'] } } as TwinScenario
    const legacy = { story: null } as TwinScenario
    const covered = coveredTargetIds([saved, legacy], [situation({ covers: ['sys:ERP'] })])
    expect([...covered].sort()).toEqual(['happy_path', 'sys:ERP'])
  })
})

describe('parseFieldValue', () => {
  it('keeps numbers numeric and falls back to text', () => {
    expect(parseFieldValue('42', 1)).toBe(42)
    expect(parseFieldValue('abc', 1)).toBe('abc')
    expect(parseFieldValue('x', 'y')).toBe('x')
  })
})
