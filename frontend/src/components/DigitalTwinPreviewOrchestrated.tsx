import { useMemo, useState } from 'react'
import type { BlueprintOverlay } from '../api/types'
import DigitalTwinBpmnView from './DigitalTwinBpmnView'
import { buildOrchestratedChain } from '../lib/digitalTwinChain'
import type { AgentGroup } from '../lib/blueprintLabels'

type View = 'chain' | 'bpmn'

// Same underlying chain and heuristics as DigitalTwinPreview, but every
// suggested agent is spliced directly into the sequence (buildOrchestratedChain)
// instead of listed separately below -- so the proposed solution shown here
// already includes the suggested coverage, not just what the blueprint
// generated on its own.
export default function DigitalTwinPreviewOrchestrated({
  overlay,
  groups,
  labelsById,
}: {
  overlay: BlueprintOverlay
  groups: AgentGroup[]
  labelsById: Record<string, string>
}) {
  const chain = useMemo(() => buildOrchestratedChain(overlay, groups), [overlay, groups])
  const suggestedItems = useMemo(() => chain.filter((item) => item.type === 'suggested'), [chain])
  const [view, setView] = useState<View>('chain')

  return (
    <div className="page twin-preview" data-testid="digital-twin-preview-orchestrated">
      <p className="twin-disclaimer">
        Hypothetical preview, not a verified simulation: follows the current blueprint's step order and tooling,
        with suggested additional agents woven directly into the sequence below as part of the proposed solution
        rather than listed separately. Real scenario-based validation is Epic 14 (Digital Twin Simulation &amp;
        Validation), not yet built.
      </p>

      <h3>Orchestrated solution</h3>
      <div className="twin-view-toggle" role="tablist" aria-label="Digital twin preview orchestrated view">
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
            <div
              className="twin-chain-entry"
              key={item.type === 'agent' ? item.group.groupKey : item.type === 'human' ? item.nodeId : item.id}
            >
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
              ) : item.type === 'human' ? (
                <div className="twin-chain-item twin-chain-item-human">
                  <span className="badge">Human</span>
                  <span className="twin-chain-item-name">{labelsById[item.nodeId] ?? item.nodeId}</span>
                </div>
              ) : (
                <div className="twin-chain-item twin-chain-item-suggested" title={item.reason}>
                  <span className="badge">Suggested</span>
                  <span className="twin-chain-item-name">{item.name}</span>
                </div>
              )}
              {i < chain.length - 1 && <span className="twin-chain-arrow">&rarr;</span>}
            </div>
          ))}
        </div>
      )}

      <h3>Why the suggested agents above were added</h3>
      {suggestedItems.length === 0 ? (
        <p className="meta">No gaps identified from the current blueprint -- nothing suggested.</p>
      ) : (
        <div className="twin-suggestion-list">
          {suggestedItems.map((item) => (
            <div className="twin-suggestion-card" key={item.id}>
              <span className="twin-suggestion-name">{item.name}</span>
              <span className="meta">{item.reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
