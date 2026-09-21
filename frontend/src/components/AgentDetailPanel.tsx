import type { AgentGroup } from '../lib/blueprintLabels'
import { GOVERNANCE_DESCRIPTIONS, STEP_TYPE_LABELS, VERDICT_LABELS } from '../lib/blueprintLabels'
import AgentArtifactActions from './AgentArtifactActions'
import PublishPanel from './PublishPanel'

// Epic 8 follow-up: the "Agents" tab's card-click detail view. Answers the
// five questions the user asked for explicitly -- what is this agent, how
// will it work, what tools does it need, why was it selected
// (explainability), and what governance does it need -- as opposed to
// BlueprintDetailPanel's override-focused framing of the same underlying
// blueprint node.
export default function AgentDetailPanel({
  group,
  labelsById,
  onGenerateAgent,
  generatingAgent,
  canGenerateAgent,
  processId,
}: {
  group: AgentGroup | null
  labelsById: Record<string, string>
  onGenerateAgent: () => Promise<void>
  generatingAgent: boolean
  canGenerateAgent: boolean
  processId: string
}) {
  if (!group) {
    return (
      <aside className="element-detail-panel">
        <p className="meta">Select an agent card to see its full profile.</p>
      </aside>
    )
  }

  const { primary, artifact } = group
  const agent = primary.agent_spec
  if (!agent) return null // groups are only ever built from nodes that have an agent_spec

  return (
    <aside className="element-detail-panel">
      <div className="element-card">
        <div className="element-header">
          <span className={`badge verdict-${primary.verdict}`}>{VERDICT_LABELS[primary.verdict]}</span>
          <span className="element-label">{agent.name}</span>
        </div>

        <section className="agent-detail-section">
          <h4>What is this agent?</h4>
          <p className="element-meta">{agent.purpose}</p>
          {group.nodeIds.length > 1 && (
            <p className="element-meta">
              <strong>Covers steps:</strong> {group.nodeIds.map((id) => labelsById[id] ?? id).join(', ')}
            </p>
          )}
        </section>

        <section className="agent-detail-section">
          <h4>How will it work?</h4>
          <p className="element-meta">
            <strong>Trigger:</strong> {agent.trigger}
          </p>
          {artifact ? (
            <pre className="agent-system-prompt">{artifact.definition.system_prompt}</pre>
          ) : (
            <p className="element-meta">
              {agent.required_inputs.length > 0 && (
                <>
                  Takes {agent.required_inputs.map((f) => f.name).join(', ')}
                  {agent.expected_outputs.length > 0 ? ', ' : '. '}
                </>
              )}
              {agent.expected_outputs.length > 0 && (
                <>and produces {agent.expected_outputs.map((f) => f.name).join(', ')}. </>
              )}
              Generate this agent to see its full run instructions (system prompt).
            </p>
          )}
        </section>

        <section className="agent-detail-section">
          <h4>What tools might it require?</h4>
          {agent.tools_systems_needed.length > 0 ? (
            <ul>
              {agent.tools_systems_needed.map((tool) => (
                <li key={tool}>{tool}</li>
              ))}
            </ul>
          ) : (
            <p className="element-meta">No external tools identified.</p>
          )}
        </section>

        <section className="agent-detail-section">
          <h4>Why was this agent selected?</h4>
          <p className="element-meta">
            <span className="badge">{STEP_TYPE_LABELS[primary.step_type] ?? primary.step_type}</span>
          </p>
          <p className="element-meta">{primary.rationale}</p>
        </section>

        <section className="agent-detail-section">
          <h4>What governance would it need?</h4>
          <p className="element-meta">{GOVERNANCE_DESCRIPTIONS[agent.human_checkpoint]}</p>
        </section>

        <AgentArtifactActions
          artifact={artifact}
          onGenerate={onGenerateAgent}
          generating={generatingAgent}
          canGenerate={canGenerateAgent}
        />

        {artifact && <PublishPanel processId={processId} artifactId={artifact.id} canPublish={canGenerateAgent} />}
      </div>
    </aside>
  )
}
