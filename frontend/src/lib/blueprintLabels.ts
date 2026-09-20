import type {
  AgentArtifact,
  BlueprintNodeResult,
  BlueprintOverlay,
  BlueprintVerdict,
  HumanCheckpoint,
} from '../api/types'

export const VERDICT_LABELS: Record<BlueprintVerdict, string> = {
  automatable: 'Automatable',
  partial: 'Partially automatable',
  not_automatable: 'Not automatable',
}

export const STEP_TYPE_LABELS: Record<string, string> = {
  data_retrieval_transformation: 'Data retrieval / transformation',
  rule_based_decision: 'Rule-based decision',
  document_generation: 'Document generation',
  communication_notification: 'Communication / notification',
  judgment_based_decision: 'Judgment-based decision',
  exception_handling: 'Exception handling',
  approval_compliance_signoff: 'Approval / compliance sign-off',
  physical_manual_action: 'Physical / manual action',
}

// US8.x/agent-cards governance framing: what a human_checkpoint implies an
// agent's operator needs to put in place, not just the raw enum value.
export const GOVERNANCE_DESCRIPTIONS: Record<HumanCheckpoint, string> = {
  none: 'Fully autonomous -- no human checkpoint. Governance should focus on monitoring and audit logging of every action it takes.',
  review_before_action:
    'Requires human review and approval before acting. Governance: a reviewer role must be assigned and able to approve or reject within the process’s normal turnaround time.',
  review_after_action:
    'Acts autonomously, then requires human review after the fact. Governance: a post-action audit trail and a defined escalation path if a reviewer flags a problem.',
  escalation_on_exception:
    'Acts autonomously within its expected cases, escalates anything outside them to a human. Governance: clear, written exception criteria and an on-call/escalation owner.',
}

export interface AgentGroup {
  groupKey: string
  nodeIds: string[]
  primary: BlueprintNodeResult
  artifact: AgentArtifact | null
}

// Shared by BlueprintPage's summary stat, AgentCardsPanel, and
// DigitalTwinPreview -- one entry per distinct agent (a consolidated group
// of nodes, US7.5, counts once), not one per node.
export function computeAgentGroups(overlay: BlueprintOverlay, artifacts: AgentArtifact[]): AgentGroup[] {
  const seen = new Set<string>()
  const groups: AgentGroup[] = []
  for (const node of overlay.nodes) {
    if (!node.agent_spec) continue
    const nodeIds =
      node.agent_spec.consolidated_from_nodes.length > 0
        ? [...node.agent_spec.consolidated_from_nodes].sort()
        : [node.node_id]
    const groupKey = nodeIds.join('|')
    if (seen.has(groupKey)) continue
    seen.add(groupKey)
    const artifact = artifacts.find((a) => a.group_key === groupKey) ?? null
    groups.push({ groupKey, nodeIds, primary: node, artifact })
  }
  return groups
}
