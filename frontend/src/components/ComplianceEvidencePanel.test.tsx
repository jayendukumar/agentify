import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ComplianceEvidence } from '../api/types'
import ComplianceEvidencePanel from './ComplianceEvidencePanel'

const getComplianceEvidence = vi.fn()

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {},
  getComplianceEvidence: (...args: unknown[]) => getComplianceEvidence(...args),
  exportComplianceEvidence: vi.fn(),
}))

const evidence: ComplianceEvidence = {
  artifact_id: 'agent-1',
  agent_name: 'Screening Agent',
  agent_version: 3,
  context_pack_id: 'ctxpack-1',
  context_stale: false,
  pinned_versions: [],
  controls: [
    { target_id: 'ctl:a', control_id: 'a', code: 'AML-4', name: 'Sanctions review', version: 1, status: 'evidenced', situations: [{ scenario_id: 's1', scenario_name: 'Near-match escalates', run_id: 'r1', run_at: null, passed: true, agent_version: 3, context_pack_id: 'ctxpack-1', current: true }] },
    { target_id: 'ctl:b', control_id: 'b', code: 'GDPR-1', name: 'Consent recorded', version: 2, status: 'missing', situations: [] },
  ],
  evidenced: 1,
  total: 2,
  disclaimer: 'Decision support, not certification.',
  generated_at: '2026-10-11T00:00:00Z',
}

describe('ComplianceEvidencePanel', () => {
  afterEach(() => vi.clearAllMocks())

  it('shows each rule with its status and the disclaimer', async () => {
    getComplianceEvidence.mockResolvedValue(evidence)
    const onLoaded = vi.fn()
    render(<ComplianceEvidencePanel processId="p1" artifactId="agent-1" onLoaded={onLoaded} />)
    expect(await screen.findByText(/1 of 2 passing on v3/)).toBeInTheDocument()
    expect(screen.getByText('Passing on this version')).toBeInTheDocument()
    expect(screen.getByText('Not tested yet')).toBeInTheDocument()
    expect(screen.getByText(/Near-match escalates/)).toBeInTheDocument()
    expect(screen.getByText('Decision support, not certification.')).toBeInTheDocument()
    expect(onLoaded).toHaveBeenCalledWith(evidence)
  })

  it('renders nothing when no rules apply', async () => {
    getComplianceEvidence.mockResolvedValue({ ...evidence, controls: [], evidenced: 0, total: 0 })
    const { container } = render(<ComplianceEvidencePanel processId="p1" artifactId="agent-1" />)
    await vi.waitFor(() => expect(getComplianceEvidence).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
