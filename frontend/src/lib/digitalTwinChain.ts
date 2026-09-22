import type { BlueprintOverlay } from '../api/types'
import type { AgentGroup } from './blueprintLabels'

export type ChainItem = { type: 'agent'; group: AgentGroup } | { type: 'human'; nodeId: string; reason: string }

// Shared by DigitalTwinPreview's chain view and its BPMN alternative view --
// one entry per distinct agent group or not-automatable node, in blueprint
// step order.
export function buildChain(overlay: BlueprintOverlay, groups: AgentGroup[]): ChainItem[] {
  const seenGroupKeys = new Set<string>()
  const items: ChainItem[] = []
  for (const node of overlay.nodes) {
    if (node.agent_spec) {
      const groupKey =
        node.agent_spec.consolidated_from_nodes.length > 0
          ? [...node.agent_spec.consolidated_from_nodes].sort().join('|')
          : node.node_id
      if (seenGroupKeys.has(groupKey)) continue
      seenGroupKeys.add(groupKey)
      const group = groups.find((g) => g.groupKey === groupKey)
      if (group) items.push({ type: 'agent', group })
    } else {
      items.push({ type: 'human', nodeId: node.node_id, reason: node.not_automatable_reason ?? node.rationale })
    }
  }
  return items
}
