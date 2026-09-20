import { useState } from 'react'
import type { AgentArtifact } from '../api/types'
import { downloadText } from '../lib/exportPng'

// Shared by BlueprintDetailPanel (Blueprint tab) and AgentDetailPanel
// (Agents tab) -- the generate/regenerate/download action for one agent's
// artifact (Epic 12), identical in both places.
export default function AgentArtifactActions({
  artifact,
  onGenerate,
  generating,
  canGenerate,
}: {
  artifact: AgentArtifact | null
  onGenerate: () => Promise<void>
  generating: boolean
  canGenerate: boolean
}) {
  const [generateError, setGenerateError] = useState<string | null>(null)

  async function handleGenerateAgent() {
    setGenerateError(null)
    try {
      await onGenerate()
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

  return (
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
          disabled={generating || !canGenerate}
          title={!canGenerate ? 'Editor access required' : undefined}
        >
          {generating ? 'Generating...' : artifact ? 'Regenerate agent' : 'Generate agent'}
        </button>
        {artifact && (
          <button type="button" className="button-secondary" onClick={handleDownloadArtifact}>
            Download definition
          </button>
        )}
      </div>
    </div>
  )
}
