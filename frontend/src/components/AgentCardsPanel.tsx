import type { AgentGroup } from '../lib/blueprintLabels'
import { VERDICT_LABELS } from '../lib/blueprintLabels'
import AgentDetailPanel from './AgentDetailPanel'

export default function AgentCardsPanel({
  groups,
  labelsById,
  selectedNodeId,
  onSelect,
  onGenerateAgent,
  generatingNodeId,
  canGenerateAgent,
}: {
  groups: AgentGroup[]
  labelsById: Record<string, string>
  selectedNodeId: string | null
  onSelect: (nodeId: string) => void
  onGenerateAgent: (nodeId: string) => Promise<void>
  generatingNodeId: string | null
  canGenerateAgent: boolean
}) {
  const selectedGroup = groups.find((g) => selectedNodeId && g.nodeIds.includes(selectedNodeId)) ?? null

  return (
    <div className="diagram-body" data-testid="agent-cards-panel">
      <div className="agent-card-grid-wrap">
        {groups.length === 0 ? (
          <p className="meta">
            No agents identified yet -- steps must be evaluated as automatable or partially automatable (see the
            Blueprint tab) before an agent card appears here.
          </p>
        ) : (
          <div className="agent-card-grid">
            {groups.map((group) => {
              const agent = group.primary.agent_spec!
              const isSelected = group.nodeIds.includes(selectedNodeId ?? '')
              return (
                <button
                  key={group.groupKey}
                  type="button"
                  className={`agent-card${isSelected ? ' agent-card-selected' : ''}`}
                  onClick={() => onSelect(group.primary.node_id)}
                >
                  <span className="agent-card-header">
                    <span className="agent-card-name">{agent.name}</span>
                    <span className={`badge verdict-${group.primary.verdict}`}>
                      {VERDICT_LABELS[group.primary.verdict]}
                    </span>
                  </span>
                  <span className="agent-card-purpose">{agent.purpose}</span>
                  <span className="agent-card-footer">
                    <span className="meta">
                      {group.nodeIds.length > 1
                        ? `${group.nodeIds.length} consolidated steps`
                        : (labelsById[group.primary.node_id] ?? group.primary.node_id)}
                    </span>
                    {group.artifact && (
                      <span className={`badge ${group.artifact.status === 'stale' ? 'confidence-medium' : 'status-done'}`}>
                        {group.artifact.status}
                      </span>
                    )}
                  </span>
                </button>
              )
            })}
          </div>
        )}
      </div>
      <AgentDetailPanel
        group={selectedGroup}
        labelsById={labelsById}
        onGenerateAgent={() => onGenerateAgent(selectedGroup?.primary.node_id ?? '')}
        generatingAgent={generatingNodeId !== null && selectedGroup?.primary.node_id === generatingNodeId}
        canGenerateAgent={canGenerateAgent}
      />
    </div>
  )
}
