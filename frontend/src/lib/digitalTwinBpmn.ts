import type { HumanCheckpoint } from '../api/types'
import type { ChainItem } from './digitalTwinChain'

// Renders the same step chain as DigitalTwinPreview's default view, but as a
// three-pool BPMN collaboration diagram (Human / Agents / Systems &
// applications) instead of a linear card list -- lets an Automation
// Architect see cross-participant connections (agent handoffs, which
// systems each agent touches, which agents carry a governance checkpoint)
// at a glance. Client-side generated, not run through the backend BPMN
// builder (`backend/app/bpmn/`) -- this diagram has no corresponding
// process-schema element, it's a derived view of the blueprint overlay.

const TASK_W = 100
const TASK_H = 80
const COL_W = 180
const COL_START_X = 160
const ROW_H = 120
const POOL_PAD = 20
const POOL_HEADER_X = 30 // left margin so the pool's rotated name label has room
const POOL_GAP = 40 // vertical clearance between pools, purely so cross-pool edges have a lane to jog through

const CHECKPOINT_ORDER: HumanCheckpoint[] = ['review_before_action', 'review_after_action', 'escalation_on_exception']
const CHECKPOINT_LABELS: Record<HumanCheckpoint, string> = {
  none: '',
  review_before_action: 'Review before action',
  review_after_action: 'Review after action',
  escalation_on_exception: 'Escalation on exception',
}

function escapeXml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function slug(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'x'
}

interface NodeLayout {
  id: string
  name: string
  kind: 'human' | 'agent' | 'system' | 'checkpoint'
  col: number
  row: number
}

interface FlowLayout {
  id: string
  kind: 'sequence' | 'message'
  sourceId: string
  targetId: string
}

// Returns null when there's nothing to diagram yet (mirrors the chain
// view's "generate a blueprint first" empty state).
export function buildDigitalTwinBpmnXml(chain: ChainItem[], labelsById: Record<string, string>): string | null {
  if (chain.length === 0) return null

  const humanNodes: NodeLayout[] = []
  const agentNodes: NodeLayout[] = []
  const systemNodes: NodeLayout[] = []
  const checkpointNodes: NodeLayout[] = []
  const nodeIdByChainIndex: string[] = []
  const systemNodeIdByName = new Map<string, string>()
  const checkpointNodeIdByType = new Map<HumanCheckpoint, string>()
  const systemColumnRowCount = new Map<number, number>()

  chain.forEach((item, i) => {
    if (item.type === 'human') {
      const id = `Task_human_${slug(item.nodeId)}`
      nodeIdByChainIndex.push(id)
      humanNodes.push({ id, name: labelsById[item.nodeId] ?? item.nodeId, kind: 'human', col: i, row: 0 })
    } else {
      const spec = item.group.primary.agent_spec!
      const id = `Task_agent_${slug(item.group.groupKey)}`
      nodeIdByChainIndex.push(id)
      agentNodes.push({ id, name: spec.name, kind: 'agent', col: i, row: 0 })

      for (const rawTool of spec.tools_systems_needed) {
        const tool = rawTool.trim()
        if (!tool || systemNodeIdByName.has(tool)) continue
        const row = systemColumnRowCount.get(i) ?? 0
        systemColumnRowCount.set(i, row + 1)
        const systemId = `Task_system_${slug(tool)}_${systemNodeIdByName.size}`
        systemNodeIdByName.set(tool, systemId)
        systemNodes.push({ id: systemId, name: tool, kind: 'system', col: i, row })
      }

      if (spec.human_checkpoint !== 'none' && !checkpointNodeIdByType.has(spec.human_checkpoint)) {
        const id = `Task_checkpoint_${slug(spec.human_checkpoint)}`
        checkpointNodeIdByType.set(spec.human_checkpoint, id)
      }
    }
  })

  CHECKPOINT_ORDER.forEach((type, idx) => {
    const id = checkpointNodeIdByType.get(type)
    if (!id) return
    checkpointNodes.push({ id, name: `Checkpoint: ${CHECKPOINT_LABELS[type]}`, kind: 'checkpoint', col: chain.length + idx, row: 0 })
  })

  const flows: FlowLayout[] = []
  let seqCounter = 0
  let msgCounter = 0
  for (let i = 0; i < chain.length - 1; i++) {
    const a = chain[i]
    const b = chain[i + 1]
    const sourceId = nodeIdByChainIndex[i]
    const targetId = nodeIdByChainIndex[i + 1]
    if (a.type === b.type) {
      flows.push({ id: `Flow_seq_${seqCounter++}`, kind: 'sequence', sourceId, targetId })
    } else {
      flows.push({ id: `Flow_msg_${msgCounter++}`, kind: 'message', sourceId, targetId })
    }
  }

  chain.forEach((item, i) => {
    if (item.type !== 'agent') return
    const spec = item.group.primary.agent_spec!
    const agentId = nodeIdByChainIndex[i]
    const seenTools = new Set<string>()
    for (const rawTool of spec.tools_systems_needed) {
      const tool = rawTool.trim()
      if (!tool || seenTools.has(tool)) continue
      seenTools.add(tool)
      const systemId = systemNodeIdByName.get(tool)
      if (systemId) flows.push({ id: `Flow_msg_${msgCounter++}`, kind: 'message', sourceId: agentId, targetId: systemId })
    }
    if (spec.human_checkpoint !== 'none') {
      const checkpointId = checkpointNodeIdByType.get(spec.human_checkpoint)
      if (checkpointId) flows.push({ id: `Flow_msg_${msgCounter++}`, kind: 'message', sourceId: agentId, targetId: checkpointId })
    }
  })

  const numColumns = chain.length + checkpointNodes.length
  const maxSystemRows = systemNodes.length > 0 ? Math.max(...systemNodes.map((n) => n.row + 1)) : 1

  const poolWidth = COL_START_X + numColumns * COL_W + 60
  const humanPoolHeight = ROW_H + 2 * POOL_PAD
  const agentsPoolHeight = ROW_H + 2 * POOL_PAD
  const systemsPoolHeight = maxSystemRows * ROW_H + 2 * POOL_PAD

  const humanPoolY = 0
  const agentsPoolY = humanPoolY + humanPoolHeight + POOL_GAP
  const systemsPoolY = agentsPoolY + agentsPoolHeight + POOL_GAP

  function poolBand(kind: NodeLayout['kind']): { top: number; height: number } {
    if (kind === 'human' || kind === 'checkpoint') return { top: humanPoolY, height: humanPoolHeight }
    if (kind === 'agent') return { top: agentsPoolY, height: agentsPoolHeight }
    return { top: systemsPoolY, height: systemsPoolHeight }
  }

  function nodeX(col: number): number {
    return POOL_HEADER_X + COL_START_X + col * COL_W
  }
  function nodeY(poolY: number, row: number): number {
    return poolY + POOL_PAD + row * ROW_H + (ROW_H - TASK_H) / 2
  }

  const allNodes: Array<NodeLayout & { x: number; y: number; bpmnType: string }> = [
    ...humanNodes.map((n) => ({ ...n, x: nodeX(n.col), y: nodeY(humanPoolY, n.row), bpmnType: 'userTask' })),
    ...checkpointNodes.map((n) => ({ ...n, x: nodeX(n.col), y: nodeY(humanPoolY, n.row), bpmnType: 'userTask' })),
    ...agentNodes.map((n) => ({ ...n, x: nodeX(n.col), y: nodeY(agentsPoolY, n.row), bpmnType: 'serviceTask' })),
    ...systemNodes.map((n) => ({ ...n, x: nodeX(n.col), y: nodeY(systemsPoolY, n.row), bpmnType: 'task' })),
  ]
  const nodeById = new Map(allNodes.map((n) => [n.id, n]))

  const humanFlowNodeIds = [...humanNodes, ...checkpointNodes].map((n) => n.id)
  const agentFlowNodeIds = agentNodes.map((n) => n.id)
  const systemFlowNodeIds = systemNodes.map((n) => n.id)

  const sequenceFlows = flows.filter((f) => f.kind === 'sequence')
  const messageFlows = flows.filter((f) => f.kind === 'message')

  function processXml(processId: string, nodes: NodeLayout[], nodeIds: string[]): string {
    const tasks = nodes
      .map((n) => {
        const layout = nodeById.get(n.id)!
        return `    <bpmn:${layout.bpmnType} id="${n.id}" name="${escapeXml(n.name)}" />`
      })
      .join('\n')
    const flowsXml = sequenceFlows
      .filter((f) => nodeIds.includes(f.sourceId) && nodeIds.includes(f.targetId))
      .map((f) => `    <bpmn:sequenceFlow id="${f.id}" sourceRef="${f.sourceId}" targetRef="${f.targetId}" />`)
      .join('\n')
    return `  <bpmn:process id="${processId}" isExecutable="false">\n${tasks}${tasks && flowsXml ? '\n' : ''}${flowsXml}\n  </bpmn:process>`
  }

  const humanProcess = processXml('Process_twin_human', [...humanNodes, ...checkpointNodes], humanFlowNodeIds)
  const agentsProcess = processXml('Process_twin_agents', agentNodes, agentFlowNodeIds)
  const systemsProcess = processXml('Process_twin_systems', systemNodes, systemFlowNodeIds)

  const messageFlowsXml = messageFlows
    .map((f) => `      <bpmn:messageFlow id="${f.id}" sourceRef="${f.sourceId}" targetRef="${f.targetId}" />`)
    .join('\n')

  const collaboration = `  <bpmn:collaboration id="Collaboration_twin">
    <bpmn:participant id="Participant_human" name="Human" processRef="Process_twin_human" />
    <bpmn:participant id="Participant_agents" name="Agents" processRef="Process_twin_agents" />
    <bpmn:participant id="Participant_systems" name="Systems &amp; applications" processRef="Process_twin_systems" />
${messageFlowsXml}
  </bpmn:collaboration>`

  function poolShape(id: string, x: number, y: number, width: number, height: number): string {
    return `      <bpmndi:BPMNShape id="Shape_${id}" bpmnElement="${id}" isHorizontal="true">
        <dc:Bounds x="${x}" y="${y}" width="${width}" height="${height}" />
      </bpmndi:BPMNShape>`
  }

  function taskShape(n: NodeLayout & { x: number; y: number }): string {
    return `      <bpmndi:BPMNShape id="Shape_${n.id}" bpmnElement="${n.id}">
        <dc:Bounds x="${n.x}" y="${n.y}" width="${TASK_W}" height="${TASK_H}" />
      </bpmndi:BPMNShape>`
  }

  // Orthogonal routing, never a straight source-center-to-target-center
  // line -- that reads fine same-row but cuts diagonally through every
  // unrelated task box in between once source and target sit in different
  // pools, which is the common case here (see bpmn-authoring skill's
  // edge-routing note, same reasoning as the backend BPMN layout).
  function edgeWaypoints(sourceId: string, targetId: string): string {
    const source = nodeById.get(sourceId)!
    const target = nodeById.get(targetId)!
    const sCenterX = source.x + TASK_W / 2
    const tCenterX = target.x + TASK_W / 2
    const sBand = poolBand(source.kind)
    const tBand = poolBand(target.kind)

    if (sBand.top === tBand.top) {
      // Same pool (sequence flows only ever connect same-row tasks here) --
      // a single straight horizontal segment.
      const y = source.y + TASK_H / 2
      return `<di:waypoint x="${sCenterX}" y="${y}" /><di:waypoint x="${tCenterX}" y="${y}" />`
    }

    // Cross-pool message flow: exit the source vertically, jog across at
    // the midline of the gap between the two (always-adjacent) pools, then
    // enter the target vertically -- an "S" shape instead of a diagonal.
    const sourceBelow = sBand.top > tBand.top
    const gapMidY = sourceBelow ? (tBand.top + tBand.height + sBand.top) / 2 : (sBand.top + sBand.height + tBand.top) / 2
    const sExitY = sourceBelow ? source.y : source.y + TASK_H
    const tEntryY = sourceBelow ? target.y + TASK_H : target.y

    if (sCenterX === tCenterX) {
      return `<di:waypoint x="${sCenterX}" y="${sExitY}" /><di:waypoint x="${tCenterX}" y="${tEntryY}" />`
    }
    return [
      `<di:waypoint x="${sCenterX}" y="${sExitY}" />`,
      `<di:waypoint x="${sCenterX}" y="${gapMidY}" />`,
      `<di:waypoint x="${tCenterX}" y="${gapMidY}" />`,
      `<di:waypoint x="${tCenterX}" y="${tEntryY}" />`,
    ].join('')
  }

  const shapesXml = [
    poolShape('Participant_human', 0, humanPoolY, poolWidth, humanPoolHeight),
    poolShape('Participant_agents', 0, agentsPoolY, poolWidth, agentsPoolHeight),
    poolShape('Participant_systems', 0, systemsPoolY, poolWidth, systemsPoolHeight),
    ...allNodes.map(taskShape),
  ].join('\n')

  const edgesXml = flows
    .map(
      (f) => `      <bpmndi:BPMNEdge id="Edge_${f.id}" bpmnElement="${f.id}">
        ${edgeWaypoints(f.sourceId, f.targetId)}
      </bpmndi:BPMNEdge>`,
    )
    .join('\n')

  return `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
    xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
    xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
    id="Definitions_twin_preview"
    targetNamespace="http://agentic-solution-generator/bpmn">
${collaboration}
${humanProcess}
${agentsProcess}
${systemsProcess}
  <bpmndi:BPMNDiagram id="Diagram_twin_preview">
    <bpmndi:BPMNPlane id="Plane_twin_preview" bpmnElement="Collaboration_twin">
${shapesXml}
${edgesXml}
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
`
}
