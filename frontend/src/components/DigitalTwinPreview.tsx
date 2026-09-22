import { useMemo, useState } from 'react'
import type { BlueprintOverlay } from '../api/types'
import DigitalTwinBpmnView from './DigitalTwinBpmnView'
import { buildChain, type ChainItem } from '../lib/digitalTwinChain'
import type { AgentGroup } from '../lib/blueprintLabels'

type View = 'chain' | 'bpmn'

interface Suggestion {
  name: string
  reason: string
}

// Deliberately simple, deterministic heuristics -- not a real simulation
// (that's Epic 14, Digital Twin Simulation & Validation, not yet built).
// Each suggestion states the specific evidence it's based on so it reads
// as a hypothesis to check, not a claim.
function buildSuggestions(chain: ChainItem[], groups: AgentGroup[]): Suggestion[] {
  const suggestions: Suggestion[] = []
  const agentItems = chain.filter((item): item is { type: 'agent'; group: AgentGroup } => item.type === 'agent')

  if (agentItems.length >= 2) {
    suggestions.push({
      name: 'Process Orchestrator Agent',
      reason: `Coordinates handoffs and sequencing across the ${agentItems.length} agents below -- none of them currently owns end-to-end sequencing or failure recovery across the whole chain.`,
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
        name: `Handoff agent: ${a.group.primary.agent_spec!.name} → ${b.group.primary.agent_spec!.name}`,
        reason: `These two agents use different systems (${[...toolsA].join(', ')} vs. ${[...toolsB].join(
          ', ',
        )}) with no tooling in common -- a dedicated handoff step may be needed to translate data between them.`,
      })
    }
  }

  const escalators = groups.filter((g) => g.primary.agent_spec?.human_checkpoint === 'escalation_on_exception')
  if (escalators.length > 0) {
    suggestions.push({
      name: 'Exception Handling Agent',
      reason: `${escalators.length} agent(s) (${escalators
        .map((g) => g.primary.agent_spec!.name)
        .join(', ')}) currently escalate exceptions straight to a human -- a shared exception-handling agent could triage these first.`,
    })
  }

  return suggestions
}

export default function DigitalTwinPreview({
  overlay,
  groups,
  labelsById,
}: {
  overlay: BlueprintOverlay
  groups: AgentGroup[]
  labelsById: Record<string, string>
}) {
  const chain = useMemo(() => buildChain(overlay, groups), [overlay, groups])
  const suggestions = useMemo(() => buildSuggestions(chain, groups), [chain, groups])
  const [view, setView] = useState<View>('chain')

  return (
    <div className="page twin-preview" data-testid="digital-twin-preview">
      <p className="twin-disclaimer">
        Hypothetical preview, not a verified simulation: this follows the current blueprint's step order and tooling
        only. Real scenario-based validation is Epic 14 (Digital Twin Simulation &amp; Validation), not yet built.
      </p>

      <h3>Connected agents</h3>
      <div className="twin-view-toggle" role="tablist" aria-label="Digital twin preview view">
        <button
          type="button"
          role="tab"
          aria-selected={view === 'chain'}
          className={`tab-button${view === 'chain' ? ' tab-button-active' : ''}`}
          onClick={() => setView('chain')}
        >
          Chain view
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === 'bpmn'}
          className={`tab-button${view === 'bpmn' ? ' tab-button-active' : ''}`}
          onClick={() => setView('bpmn')}
        >
          BPMN view
        </button>
      </div>
      {view === 'bpmn' ? (
        <DigitalTwinBpmnView chain={chain} labelsById={labelsById} />
      ) : chain.length === 0 ? (
        <p className="meta">No steps evaluated yet -- generate a blueprint first.</p>
      ) : (
        <div className="twin-chain">
          {chain.map((item, i) => (
            <div className="twin-chain-entry" key={item.type === 'agent' ? item.group.groupKey : item.nodeId}>
              {item.type === 'agent' ? (
                <div className="twin-chain-item twin-chain-item-agent">
                  <span className="badge status-done">Agent</span>
                  <span className="twin-chain-item-name">{item.group.primary.agent_spec!.name}</span>
                  {item.group.artifact && (
                    <span
                      className={`badge ${item.group.artifact.status === 'stale' ? 'confidence-medium' : ''}`}
                    >
                      {item.group.artifact.status}
                    </span>
                  )}
                </div>
              ) : (
                <div className="twin-chain-item twin-chain-item-human">
                  <span className="badge">Human</span>
                  <span className="twin-chain-item-name">{labelsById[item.nodeId] ?? item.nodeId}</span>
                </div>
              )}
              {i < chain.length - 1 && <span className="twin-chain-arrow">&rarr;</span>}
            </div>
          ))}
        </div>
      )}

      <h3>Suggested additional agents for a fuller digital twin</h3>
      {suggestions.length === 0 ? (
        <p className="meta">No gaps identified from the current blueprint -- nothing to suggest yet.</p>
      ) : (
        <div className="twin-suggestion-list">
          {suggestions.map((suggestion) => (
            <div className="twin-suggestion-card" key={suggestion.name}>
              <span className="twin-suggestion-name">{suggestion.name}</span>
              <span className="meta">{suggestion.reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
