import { describe, expect, it } from 'vitest'
import type { AgentSpec, BlueprintNodeResult, BlueprintOverlay } from '../api/types'
import type { AgentGroup } from './blueprintLabels'
import { buildChain, buildOrchestratedChain, buildSuggestedAgents } from './digitalTwinChain'

function agentSpec(overrides: Partial<AgentSpec> = {}): AgentSpec {
  return {
    name: 'Agent A',
    purpose: 'purpose',
    trigger: 'trigger',
    required_inputs: [],
    expected_outputs: [],
    tools_systems_needed: [],
    human_checkpoint: 'none',
    consolidated_from_nodes: [],
    ...overrides,
  }
}

function node(nodeId: string, spec: AgentSpec | null, notAutomatableReason = 'Needs a human'): BlueprintNodeResult {
  return {
    node_id: nodeId,
    verdict: spec ? 'automatable' : 'not_automatable',
    step_type: 'data_retrieval_transformation',
    rationale: 'mechanical',
    agent_spec: spec,
    not_automatable_reason: spec ? null : notAutomatableReason,
    overridden: false,
    override_justification: null,
    overridden_by: null,
    overridden_by_name: null,
  }
}

function overlayOf(nodes: BlueprintNodeResult[]): BlueprintOverlay {
  return { process_id: 'proc-1', baseline_version_id: 'ver-1', generated_at: '2026-01-01T00:00:00Z', nodes }
}

function groupsOf(overlay: BlueprintOverlay): AgentGroup[] {
  return overlay.nodes
    .filter((n) => n.agent_spec)
    .map((n) => ({ groupKey: n.node_id, nodeIds: [n.node_id], primary: n, artifact: null }))
}

describe('buildSuggestedAgents', () => {
  it('suggests a Process Orchestrator only once there are at least two agents', () => {
    const overlay = overlayOf([node('a', agentSpec({ name: 'Agent A' }))])
    const groups = groupsOf(overlay)
    const chain = buildChain(overlay, groups)
    expect(buildSuggestedAgents(chain, groups)).toEqual([])

    const overlay2 = overlayOf([node('a', agentSpec({ name: 'Agent A' })), node('b', agentSpec({ name: 'Agent B' }))])
    const groups2 = groupsOf(overlay2)
    const chain2 = buildChain(overlay2, groups2)
    const suggestions = buildSuggestedAgents(chain2, groups2)
    expect(suggestions.find((s) => s.id === 'suggested-orchestrator')).toMatchObject({ insertAfterIndex: -1 })
  })

  it('suggests a handoff agent between two consecutive agents with no tooling overlap', () => {
    const overlay = overlayOf([
      node('a', agentSpec({ name: 'Agent A', tools_systems_needed: ['CRM'] })),
      node('b', agentSpec({ name: 'Agent B', tools_systems_needed: ['ERP'] })),
    ])
    const groups = groupsOf(overlay)
    const chain = buildChain(overlay, groups)
    const suggestions = buildSuggestedAgents(chain, groups)
    const handoff = suggestions.find((s) => s.id === 'suggested-handoff-0')
    expect(handoff).toMatchObject({ insertAfterIndex: 0 })
    expect(handoff!.name).toContain('Agent A')
    expect(handoff!.name).toContain('Agent B')
  })

  it('does not suggest a handoff agent when consecutive agents share tooling', () => {
    const overlay = overlayOf([
      node('a', agentSpec({ name: 'Agent A', tools_systems_needed: ['CRM'] })),
      node('b', agentSpec({ name: 'Agent B', tools_systems_needed: ['CRM'] })),
    ])
    const groups = groupsOf(overlay)
    const chain = buildChain(overlay, groups)
    expect(buildSuggestedAgents(chain, groups).some((s) => s.id.startsWith('suggested-handoff'))).toBe(false)
  })

  it('suggests one shared Exception Handling Agent placed after the last escalating agent', () => {
    const overlay = overlayOf([
      node('a', agentSpec({ name: 'Agent A', human_checkpoint: 'escalation_on_exception' })),
      node('b', agentSpec({ name: 'Agent B' })),
      node('c', agentSpec({ name: 'Agent C', human_checkpoint: 'escalation_on_exception' })),
    ])
    const groups = groupsOf(overlay)
    const chain = buildChain(overlay, groups)
    const suggestions = buildSuggestedAgents(chain, groups)
    const exceptionHandler = suggestions.filter((s) => s.id === 'suggested-exception-handler')
    expect(exceptionHandler).toHaveLength(1)
    expect(exceptionHandler[0].insertAfterIndex).toBe(2) // after Agent C, the last escalator
    expect(exceptionHandler[0].reason).toContain('Agent A')
    expect(exceptionHandler[0].reason).toContain('Agent C')
  })
})

describe('buildOrchestratedChain', () => {
  it('splices each suggestion in right after the chain position it applies to', () => {
    const overlay = overlayOf([
      node('a', agentSpec({ name: 'Agent A', human_checkpoint: 'escalation_on_exception' })),
      node('b', null),
    ])
    const groups = groupsOf(overlay)

    const orchestrated = buildOrchestratedChain(overlay, groups)

    // No Process Orchestrator (only one agent), but the exception handler
    // belongs right after Agent A (index 0), so: Agent A, suggested, human.
    expect(orchestrated.map((item) => item.type)).toEqual(['agent', 'suggested', 'human'])
    expect(orchestrated[1]).toMatchObject({ type: 'suggested', name: 'Exception Handling Agent' })
  })

  it('prepends a suggested Process Orchestrator before the whole chain', () => {
    const overlay = overlayOf([node('a', agentSpec({ name: 'Agent A' })), node('b', agentSpec({ name: 'Agent B' }))])
    const groups = groupsOf(overlay)

    const orchestrated = buildOrchestratedChain(overlay, groups)

    expect(orchestrated[0]).toMatchObject({ type: 'suggested', name: 'Process Orchestrator Agent' })
    expect(orchestrated.slice(1).map((item) => item.type)).toEqual(['agent', 'agent'])
  })

  it('returns exactly buildChain’s result when there is nothing to suggest', () => {
    const overlay = overlayOf([node('a', agentSpec({ name: 'Agent A' }))])
    const groups = groupsOf(overlay)

    expect(buildOrchestratedChain(overlay, groups)).toEqual(buildChain(overlay, groups))
  })
})
