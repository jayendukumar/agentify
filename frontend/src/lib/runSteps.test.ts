import { describe, expect, it } from 'vitest'
import type { RunStep } from '../api/types'
import { cumulativeAt, narrateStep, stepDurationMs, stepsForView } from './runSteps'

function step(overrides: Partial<RunStep>): RunStep {
  return {
    seq: 0,
    type: 'MODEL_INVOKED',
    node_id: null,
    target: null,
    arguments: {},
    result: null,
    decision: null,
    reason: null,
    denied: false,
    static_fallback: false,
    tokens: null,
    cost_usd: null,
    occurred_at: '2026-01-01T00:00:00.000Z',
    ...overrides,
  }
}

describe('narrateStep', () => {
  it('narrates business-relevant step types', () => {
    expect(narrateStep(step({ type: 'RUN_STARTED' }))).toBe('Run started')
    expect(narrateStep(step({ type: 'TOOL_EXECUTED', target: 'CRM API' }))).toBe('Looked up CRM API')
    expect(narrateStep(step({ type: 'HUMAN_APPROVED' }))).toBe('Approved by a human reviewer')
  })

  it('returns null for purely technical step types', () => {
    expect(narrateStep(step({ type: 'MODEL_INVOKED' }))).toBeNull()
    expect(narrateStep(step({ type: 'CONTEXT_RETRIEVED' }))).toBeNull()
    expect(narrateStep(step({ type: 'POLICY_CHECK' }))).toBeNull()
  })
})

describe('stepsForView', () => {
  const steps = [
    step({ seq: 0, type: 'RUN_STARTED' }),
    step({ seq: 1, type: 'MODEL_INVOKED' }),
    step({ seq: 2, type: 'POLICY_CHECK' }),
    step({ seq: 3, type: 'TOOL_EXECUTED', target: 'CRM API' }),
    step({ seq: 4, type: 'HUMAN_APPROVED' }),
  ]

  it('technical view includes every step, unfiltered', () => {
    expect(stepsForView(steps, 'technical')).toHaveLength(5)
  })

  it('agent view keeps only decision/policy/human-checkpoint step types', () => {
    const result = stepsForView(steps, 'agent')
    expect(result.map((s) => s.type)).toEqual(['POLICY_CHECK', 'HUMAN_APPROVED'])
  })

  it('process view keeps only narratable step types', () => {
    const result = stepsForView(steps, 'process')
    expect(result.map((s) => s.type)).toEqual(['RUN_STARTED', 'TOOL_EXECUTED', 'HUMAN_APPROVED'])
  })
})

describe('cumulativeAt', () => {
  const steps = [
    step({ seq: 0, type: 'RUN_STARTED', occurred_at: '2026-01-01T00:00:00.000Z' }),
    step({ seq: 1, type: 'MODEL_INVOKED', tokens: 100, cost_usd: 0.01, occurred_at: '2026-01-01T00:00:01.000Z' }),
    step({ seq: 2, type: 'TOOL_EXECUTED', tokens: 50, cost_usd: 0.005, occurred_at: '2026-01-01T00:00:02.000Z' }),
  ]

  it('sums tokens/cost and computes elapsed time up to and including seq', () => {
    const result = cumulativeAt(steps, 1)
    expect(result.tokens).toBe(100)
    expect(result.costUsd).toBeCloseTo(0.01)
    expect(result.elapsedMs).toBe(1000)
  })

  it('includes every prior step when scrubbed to the last one', () => {
    const result = cumulativeAt(steps, 2)
    expect(result.tokens).toBe(150)
    expect(result.costUsd).toBeCloseTo(0.015)
  })

  it('reports cost as unknown once any usage-bearing step has an unpriced cost', () => {
    const withUnknownCost = [
      ...steps.slice(0, 2),
      step({ seq: 2, type: 'TOOL_EXECUTED', tokens: 50, cost_usd: null, occurred_at: '2026-01-01T00:00:02.000Z' }),
    ]
    const result = cumulativeAt(withUnknownCost, 2)
    expect(result.costUsd).toBeNull()
  })
})

describe('stepDurationMs', () => {
  const steps = [
    step({ seq: 0, occurred_at: '2026-01-01T00:00:00.000Z' }),
    step({ seq: 1, occurred_at: '2026-01-01T00:00:02.500Z' }),
  ]

  it('measures the gap until the next step', () => {
    expect(stepDurationMs(steps, 0)).toBe(2500)
  })

  it('returns null for the run\'s last step -- nothing recorded after it', () => {
    expect(stepDurationMs(steps, 1)).toBeNull()
  })

  it('returns null for an unknown seq', () => {
    expect(stepDurationMs(steps, 99)).toBeNull()
  })
})
