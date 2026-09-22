import { describe, expect, it } from 'vitest'
import type { AgentSpec, BlueprintNodeResult } from '../api/types'
import type { AgentGroup } from './blueprintLabels'
import { buildDigitalTwinBpmnXml } from './digitalTwinBpmn'
import type { ChainItem } from './digitalTwinChain'

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

function agentGroup(groupKey: string, spec: AgentSpec): AgentGroup {
  const node: BlueprintNodeResult = {
    node_id: groupKey,
    verdict: 'automatable',
    step_type: 'data_retrieval_transformation',
    rationale: 'mechanical',
    agent_spec: spec,
    not_automatable_reason: null,
    overridden: false,
    override_justification: null,
    overridden_by: null,
    overridden_by_name: null,
  }
  return { groupKey, nodeIds: [groupKey], primary: node, artifact: null }
}

function parse(xml: string): Document {
  return new DOMParser().parseFromString(xml, 'application/xml')
}

function idsOf(doc: Document, selector: string): string[] {
  return Array.from(doc.querySelectorAll(selector)).map((el) => el.getAttribute('id')!)
}

describe('buildDigitalTwinBpmnXml', () => {
  it('returns null for an empty chain', () => {
    expect(buildDigitalTwinBpmnXml([], {})).toBeNull()
  })

  it('emits one participant per pool and a task per chain item', () => {
    const chain: ChainItem[] = [
      { type: 'agent', group: agentGroup('a', agentSpec({ name: 'Agent A', tools_systems_needed: ['CRM'] })) },
      { type: 'human', nodeId: 'b', reason: 'Needs sign-off' },
    ]
    const xml = buildDigitalTwinBpmnXml(chain, { b: 'Manager sign-off' })
    expect(xml).not.toBeNull()
    const doc = parse(xml!)
    expect(doc.querySelector('parsererror')).toBeNull()

    const participants = Array.from(doc.querySelectorAll('participant')).map((el) => el.getAttribute('name'))
    expect(participants).toEqual(['Human', 'Agents', 'Systems & applications'])

    expect(doc.querySelector('serviceTask[name="Agent A"]')).not.toBeNull()
    expect(doc.querySelector('userTask[name="Manager sign-off"]')).not.toBeNull()
    expect(doc.querySelector('task[name="CRM"]')).not.toBeNull()
  })

  it('connects consecutive same-type steps with sequenceFlow and cross-type steps with messageFlow', () => {
    const chain: ChainItem[] = [
      { type: 'agent', group: agentGroup('a', agentSpec({ name: 'Agent A' })) },
      { type: 'agent', group: agentGroup('b', agentSpec({ name: 'Agent B' })) },
      { type: 'human', nodeId: 'c', reason: 'Needs sign-off' },
    ]
    const doc = parse(buildDigitalTwinBpmnXml(chain, {})!)

    const sequenceFlows = doc.querySelectorAll('sequenceFlow')
    expect(sequenceFlows).toHaveLength(1)
    expect(sequenceFlows[0].getAttribute('sourceRef')).toBe('Task_agent_a')
    expect(sequenceFlows[0].getAttribute('targetRef')).toBe('Task_agent_b')

    const messageFlows = Array.from(doc.querySelectorAll('messageFlow'))
    expect(messageFlows.some((f) => f.getAttribute('sourceRef') === 'Task_agent_b')).toBe(true)
  })

  it('dedupes a system used by multiple agents into a single node with a message flow from each', () => {
    const chain: ChainItem[] = [
      { type: 'agent', group: agentGroup('a', agentSpec({ name: 'Agent A', tools_systems_needed: ['CRM'] })) },
      { type: 'agent', group: agentGroup('b', agentSpec({ name: 'Agent B', tools_systems_needed: ['CRM'] })) },
    ]
    const doc = parse(buildDigitalTwinBpmnXml(chain, {})!)

    const systemTasks = doc.querySelectorAll('task[name="CRM"]')
    expect(systemTasks).toHaveLength(1)
    const systemId = systemTasks[0].getAttribute('id')!
    const messageFlowsToSystem = Array.from(doc.querySelectorAll('messageFlow')).filter(
      (f) => f.getAttribute('targetRef') === systemId,
    )
    expect(messageFlowsToSystem.map((f) => f.getAttribute('sourceRef')).sort()).toEqual(['Task_agent_a', 'Task_agent_b'])
  })

  it('creates one shared checkpoint task per checkpoint type, not one per agent', () => {
    const chain: ChainItem[] = [
      { type: 'agent', group: agentGroup('a', agentSpec({ name: 'Agent A', human_checkpoint: 'escalation_on_exception' })) },
      { type: 'agent', group: agentGroup('b', agentSpec({ name: 'Agent B', human_checkpoint: 'escalation_on_exception' })) },
    ]
    const doc = parse(buildDigitalTwinBpmnXml(chain, {})!)

    const checkpointTasks = doc.querySelectorAll('userTask[name="Checkpoint: Escalation on exception"]')
    expect(checkpointTasks).toHaveLength(1)
    const checkpointId = checkpointTasks[0].getAttribute('id')!
    const messageFlowsToCheckpoint = Array.from(doc.querySelectorAll('messageFlow')).filter(
      (f) => f.getAttribute('targetRef') === checkpointId,
    )
    expect(messageFlowsToCheckpoint).toHaveLength(2)
  })

  it('gives every DI shape and edge a bpmnElement that exists in the semantic model', () => {
    const chain: ChainItem[] = [
      {
        type: 'agent',
        group: agentGroup(
          'a',
          agentSpec({ name: 'Agent A', tools_systems_needed: ['CRM'], human_checkpoint: 'review_before_action' }),
        ),
      },
      { type: 'human', nodeId: 'b', reason: 'Needs sign-off' },
    ]
    const doc = parse(buildDigitalTwinBpmnXml(chain, {})!)

    const semanticIds = new Set([
      ...idsOf(doc, 'process'),
      ...idsOf(doc, 'participant'),
      ...idsOf(doc, 'userTask, serviceTask, task'),
      ...idsOf(doc, 'sequenceFlow, messageFlow'),
      'Collaboration_twin',
    ])
    const diRefs = Array.from(doc.querySelectorAll('BPMNShape, BPMNEdge')).map((el) => el.getAttribute('bpmnElement')!)
    for (const ref of diRefs) {
      expect(semanticIds.has(ref)).toBe(true)
    }
  })
})
