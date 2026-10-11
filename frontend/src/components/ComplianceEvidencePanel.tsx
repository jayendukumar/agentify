import { useEffect, useState } from 'react'
import { ApiError, exportComplianceEvidence, getComplianceEvidence } from '../api/client'
import type { ComplianceEvidence, ControlEvidence } from '../api/types'
import { downloadText } from '../lib/exportPng'

const STATUS: Record<ControlEvidence['status'], { label: string; badge: string }> = {
  evidenced: { label: 'Passing on this version', badge: 'status-done' },
  out_of_date: { label: 'Passed before -- re-run on this version', badge: 'confidence-medium' },
  failing: { label: 'Latest run failed', badge: 'status-failed' },
  missing: { label: 'Not tested yet', badge: 'confidence-low' },
}

/** Epic 30, US30.6/US30.7: each rule the agent follows, mapped to its
 * passing situations -- what the release gate checks, and what goes in
 * the audit file. */
export default function ComplianceEvidencePanel({
  processId,
  artifactId,
  onLoaded,
}: {
  processId: string
  artifactId: string
  onLoaded?: (evidence: ComplianceEvidence) => void
}) {
  const [evidence, setEvidence] = useState<ComplianceEvidence | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getComplianceEvidence(processId, artifactId)
      .then((result) => {
        setEvidence(result)
        onLoaded?.(result)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load compliance evidence'))
  }, [processId, artifactId])

  async function handleExport() {
    try {
      downloadText(await exportComplianceEvidence(processId, artifactId), `compliance-evidence-${artifactId}.md`, 'text/markdown')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Export failed')
    }
  }

  if (error) return <p className="error" role="alert">{error}</p>
  if (!evidence || evidence.total === 0) return null

  return (
    <div className="compliance-evidence" data-testid="compliance-evidence">
      <p className="element-meta">
        <strong>Rules tested:</strong> {evidence.evidenced} of {evidence.total} passing on v{evidence.agent_version}
        {evidence.context_stale && ' -- rules have changed since this agent was built; regenerate or re-run'}
      </p>
      <ul>
        {evidence.controls.map((control) => (
          <li key={control.control_id}>
            <span className={`badge ${STATUS[control.status].badge}`}>{STATUS[control.status].label}</span>{' '}
            {control.code ? `${control.code}: ` : ''}
            {control.name}
            {control.situations.length > 0 && (
              <span className="meta"> -- {control.situations.map((s) => s.scenario_name).join(', ')}</span>
            )}
          </li>
        ))}
      </ul>
      <p className="meta">{evidence.disclaimer}</p>
      <button type="button" className="button-secondary" onClick={handleExport}>Export evidence</button>
    </div>
  )
}
