import type { BlueprintOverlay } from '../api/types'
import type { AgentGroup } from './blueprintLabels'

export type ChainItem =
  | { type: 'agent'; group: AgentGroup }
  | { type: 'human'; nodeId: string; reason: string }
  | { type: 'suggested'; id: string; name: string; reason: string }

// buildChain's return type -- narrower than ChainItem so every existing
// consumer (DigitalTwinPreview, buildDigitalTwinBpmnXml called from it)
// keeps getting a compile-time guarantee that 'suggested' never appears,
// rather than having to defensively handle a case that can't happen.
// Only buildOrchestratedChain below actually produces 'suggested' items.
export type BaseChainItem = Exclude<ChainItem, { type: 'suggested' }>

// Shared by DigitalTwinPreview's chain view and its BPMN alternative view --
// one entry per distinct agent group or not-automatable node, in blueprint
// step order.
export function buildChain(overlay: BlueprintOverlay, groups: AgentGroup[]): BaseChainItem[] {
  const seenGroupKeys = new Set<string>()
  const items: BaseChainItem[] = []
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

export interface SuggestedAgent {
  id: string
  name: string
  reason: string
  // -1 means "before the whole chain" (e.g. a process-wide orchestrator);
  // otherwise the suggestion belongs right after chain[insertAfterIndex].
  insertAfterIndex: number
}

// Deliberately simple, deterministic heuristics -- not a real simulation
// (that's Epic 14, Digital Twin Simulation & Validation, not yet built).
// Each suggestion states the specific evidence it's based on so it reads
// as a hypothesis to check, not a claim. Shared by DigitalTwinPreview (as
// a separate list) and buildOrchestratedChain (spliced inline) so the two
// views can never disagree about what's suggested or why.
export function buildSuggestedAgents(chain: ChainItem[], groups: AgentGroup[]): SuggestedAgent[] {
  const suggestions: SuggestedAgent[] = []
  const agentItems = chain.filter((item): item is { type: 'agent'; group: AgentGroup } => item.type === 'agent')

  if (agentItems.length >= 2) {
    suggestions.push({
      id: 'suggested-orchestrator',
      name: 'Process Orchestrator Agent',
      reason: `Coordinates handoffs and sequencing across the ${agentItems.length} agents below -- none of them currently owns end-to-end sequencing or failure recovery across the whole chain.`,
      insertAfterIndex: -1,
    })
  }

  for (let i = 0; i < chain.length - 1; i++) {
    const a = chain[i]
    const b = chain[i + 1]
    if (a.type !== 'agent' || b.type !== 'agent') continue
    const toolsA = new Set(a.group.primary.agent_spec!.tools_systems_needed)
    const toolsB = new Set(b.group.primary.agent_spec!.tools_systems_needed)
    if (toolsA.size === 0 || toolsB.size === 0) continue
    const sharesTooling = [...toolsA].some((tool) => toolsB.has(tool))
    if (!sharesTooling) {
      suggestions.push({
        id: `suggested-handoff-${i}`,
        name: `Handoff agent: ${a.group.primary.agent_spec!.name} → ${b.group.primary.agent_spec!.name}`,
        reason: `These two agents use different systems (${[...toolsA].join(', ')} vs. ${[...toolsB].join(
          ', ',
        )}) with no tooling in common -- a dedicated handoff step may be needed to translate data between them.`,
        insertAfterIndex: i,
      })
    }
  }

  const escalators = groups.filter((g) => g.primary.agent_spec?.human_checkpoint === 'escalation_on_exception')
  if (escalators.length > 0) {
    let lastEscalatorIndex = -1
    chain.forEach((item, i) => {
      if (item.type === 'agent' && item.group.primary.agent_spec?.human_checkpoint === 'escalation_on_exception') {
        lastEscalatorIndex = i
      }
    })
    suggestions.push({
      id: 'suggested-exception-handler',
      name: 'Exception Handling Agent',
      reason: `${escalators.length} agent(s) (${escalators
        .map((g) => g.primary.agent_spec!.name)
        .join(', ')}) currently escalate exceptions straight to a human -- a shared exception-handling agent could triage these first.`,
      insertAfterIndex: lastEscalatorIndex,
    })
  }

  return suggestions
}

// The "Orchestrated" preview: the same chain as buildChain, but with each
// suggested agent spliced in at the specific point it applies to instead
// of listed separately below -- so the suggestions read as part of the
// proposed solution's actual flow, not a disconnected afterthought.
export function buildOrchestratedChain(overlay: BlueprintOverlay, groups: AgentGroup[]): ChainItem[] {
  const chain = buildChain(overlay, groups)
  const suggestions = buildSuggestedAgents(chain, groups)

  const byInsertIndex = new Map<number, SuggestedAgent[]>()
  for (const suggestion of suggestions) {
    const bucket = byInsertIndex.get(suggestion.insertAfterIndex) ?? []
    bucket.push(suggestion)
    byInsertIndex.set(suggestion.insertAfterIndex, bucket)
  }
  const toChainItems = (bucket: SuggestedAgent[] | undefined): ChainItem[] =>
    (bucket ?? []).map((s) => ({ type: 'suggested', id: s.id, name: s.name, reason: s.reason }))

  const result: ChainItem[] = [...toChainItems(byInsertIndex.get(-1))]
  chain.forEach((item, i) => {
    result.push(item)
    result.push(...toChainItems(byInsertIndex.get(i)))
  })
  return result
}
