import { useState } from 'react'
import type { AgentArtifact, BlueprintNodeResult, BlueprintVerdict } from '../api/types'
import { downloadText } from '../lib/exportPng'

const VERDICT_LABELS: Record<BlueprintVerdict, string> = {
  automatable: 'Automatable',
  partial: 'Partially automatable',
  not_automatable: 'Not automatable',
}

const STEP_TYPE_LABELS: Record<string, string> = {
  data_retrieval_transformation: 'Data retrieval / transformation',
  rule_based_decision: 'Rule-based decision',
  document_generation: 'Document generation',
  communication_notification: 'Communication / notification',
  judgment_based_decision: 'Judgment-based decision',
  exception_handling: 'Exception handling',
  approval_compliance_signoff: 'Approval / compliance sign-off',
  physical_manual_action: 'Physical / manual action',
}

export default function BlueprintDetailPanel({
  node,
  label,
  labelsById,
  onOverride,
  overriding,
  canOverride,
  artifact,
  onGenerateAgent,
  generatingAgent,
  canGenerateAgent,
}: {
  node: BlueprintNodeResult | null
  label: string | null
  labelsById: Record<string, string>
  onOverride: (verdict: BlueprintVerdict, justification: string) => Promise<void>
  overriding: boolean
  canOverride: boolean
  artifact: AgentArtifact | null
  onGenerateAgent: () => Promise<void>
  generatingAgent: boolean
  canGenerateAgent: boolean
}) {
  const [overrideOpen, setOverrideOpen] = useState(false)
  const [verdict, setVerdict] = useState<BlueprintVerdict>('automatable')
  const [justification, setJustification] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [generateError, setGenerateError] = useState<string | null>(null)

  if (!node) {
    return (
      <aside className="element-detail-panel">
        <p className="meta">Select a node on the diagram to see its automation assessment.</p>
      </aside>
    )
  }

  async function handleSubmitOverride(event: React.FormEvent) {
    event.preventDefault()
    if (!justification.trim()) {
      setError('Justification is required to override an assessment.')
      return
    }
    setError(null)
    try {
      await onOverride(verdict, justification.trim())
      setOverrideOpen(false)
      setJustification('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save override')
    }
  }

  async function handleGenerateAgent() {
    setGenerateError(null)
    try {
      await onGenerateAgent()
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : 'Failed to generate agent artifact')
    }
  }

  function handleDownloadArtifact() {
    if (!artifact) return
    downloadText(
      JSON.stringify(artifact.definition, null, 2),
      `${artifact.definition.name.replace(/\s+/g, '_').toLowerCase()}.json`,
      'application/json',
    )
  }

  const agent = node.agent_spec

  return (
    <aside className="element-detail-panel">
      <div className="element-card">
        <div className="element-header">
          <span className={`badge verdict-${node.verdict}`}>{VERDICT_LABELS[node.verdict]}</span>
          <span className="element-label">{label ?? node.node_id}</span>
          {node.overridden && <span className="badge">overridden</span>}
        </div>
        <div className="element-meta">{STEP_TYPE_LABELS[node.step_type] ?? node.step_type}</div>
        <div className="element-meta">{node.rationale}</div>

        {node.overridden && node.override_justification && (
          <div className="element-meta">
            <strong>Override justification:</strong> {node.override_justification}
            {node.overridden_by_name ? ` (by ${node.overridden_by_name})` : ''}
          </div>
        )}

        {node.verdict === 'not_automatable' && node.not_automatable_reason && (
          <div className="element-meta">
            <strong>Why not:</strong> {node.not_automatable_reason}
          </div>
        )}

        {agent && (
          <div className="agent-spec">
            <h4>{agent.name}</h4>
            <div className="element-meta">{agent.purpose}</div>
            <div className="element-meta">
              <strong>Trigger:</strong> {agent.trigger}
            </div>
            {agent.required_inputs.length > 0 && (
              <div className="element-meta">
                <strong>Required inputs:</strong>
                <ul>
                  {agent.required_inputs.map((field, i) => (
                    <li key={i}>
                      {field.name} &mdash; from {field.source_or_destination} ({field.format})
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {agent.expected_outputs.length > 0 && (
              <div className="element-meta">
                <strong>Expected outputs:</strong>
                <ul>
                  {agent.expected_outputs.map((field, i) => (
                    <li key={i}>
                      {field.name} &mdash; to {field.source_or_destination} ({field.format})
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {agent.tools_systems_needed.length > 0 && (
              <div className="element-meta">
                <strong>Tools/systems:</strong> {agent.tools_systems_needed.join(', ')}
              </div>
            )}
            <div className="element-meta">
              <strong>Human checkpoint:</strong> {agent.human_checkpoint.replace(/_/g, ' ')}
            </div>
            {agent.consolidated_from_nodes.length > 1 && (
              <div className="element-meta">
                <strong>Consolidated from:</strong>{' '}
                {agent.consolidated_from_nodes.map((id) => labelsById[id] ?? id).join(', ')}
              </div>
            )}

            <div className="agent-artifact">
              {artifact && (
                <div className="element-meta">
                  <strong>Agent artifact:</strong>{' '}
                  <span className={`badge ${artifact.status === 'stale' ? 'confidence-medium' : 'status-done'}`}>
                    {artifact.status === 'stale' ? 'stale -- regenerate' : 'generated'}
                  </span>
                </div>
              )}
              {generateError && <p className="error">{generateError}</p>}
              <div className="agent-artifact-actions">
                <button
                  type="button"
                  onClick={handleGenerateAgent}
                  disabled={generatingAgent || !canGenerateAgent}
                  title={!canGenerateAgent ? 'Editor access required' : undefined}
                >
                  {generatingAgent ? 'Generating...' : artifact ? 'Regenerate agent' : 'Generate agent'}
                </button>
                {artifact && (
                  <button type="button" className="button-secondary" onClick={handleDownloadArtifact}>
                    Download definition
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {error && <p className="error">{error}</p>}

        {overrideOpen ? (
          <form className="blueprint-override-form" onSubmit={handleSubmitOverride}>
            <label>
              Corrected verdict
              <select value={verdict} onChange={(event) => setVerdict(event.target.value as BlueprintVerdict)}>
                <option value="automatable">Automatable</option>
                <option value="partial">Partially automatable</option>
                <option value="not_automatable">Not automatable</option>
              </select>
            </label>
            <label>
              Justification
              <textarea
                value={justification}
                onChange={(event) => setJustification(event.target.value)}
                rows={3}
                placeholder="Why is the system's assessment wrong for this step?"
              />
            </label>
            <div className="blueprint-override-actions">
              <button type="submit" disabled={overriding}>
                {overriding ? 'Saving...' : 'Save override'}
              </button>
              <button type="button" onClick={() => setOverrideOpen(false)} disabled={overriding}>
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <button
            type="button"
            onClick={() => {
              setVerdict(node.verdict)
              setOverrideOpen(true)
            }}
            disabled={!canOverride}
            title={!canOverride ? 'Editor access required' : undefined}
          >
            Override assessment
          </button>
        )}
      </div>
    </aside>
  )
}
