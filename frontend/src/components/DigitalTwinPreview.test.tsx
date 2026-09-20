import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { BlueprintNodeResult, BlueprintOverlay, HumanCheckpoint } from '../api/types'
import { computeAgentGroups } from '../lib/blueprintLabels'
import DigitalTwinPreview from './DigitalTwinPreview'

function agentNode(id: string, name: string, tools: string[], humanCheckpoint: HumanCheckpoint = 'none'): BlueprintNodeResult {
  return {
    node_id: id,
    verdict: 'automatable',
    step_type: 'data_retrieval_transformation',
    rationale: `${id} is mechanical.`,
    agent_spec: {
      name,
      purpose: `${name}'s purpose`,
      trigger: 'Upstream step completes',
      required_inputs: [],
      expected_outputs: [],
      tools_systems_needed: tools,
      human_checkpoint: humanCheckpoint,
      consolidated_from_nodes: [],
    },
    not_automatable_reason: null,
    overridden: false,
    override_justification: null,
    overridden_by: null,
    overridden_by_name: null,
  }
}

function humanNode(id: string, reason: string): BlueprintNodeResult {
  return {
    node_id: id,
    verdict: 'not_automatable',
    step_type: 'approval_compliance_signoff',
    rationale: reason,
    agent_spec: null,
    not_automatable_reason: reason,
    overridden: false,
    override_justification: null,
    overridden_by: null,
    overridden_by_name: null,
  }
}

function overlayOf(nodes: BlueprintNodeResult[]): BlueprintOverlay {
  return { process_id: 'proc-1', baseline_version_id: 'ver-1', generated_at: '2026-01-01T00:00:00Z', nodes }
}

describe('DigitalTwinPreview', () => {
  it('renders the chain in step order, alternating agent and human blocks', () => {
    const overlay = overlayOf([agentNode('a', 'Agent A', ['CRM']), humanNode('b', 'Needs sign-off')])
    render(<DigitalTwinPreview overlay={overlay} groups={computeAgentGroups(overlay, [])} labelsById={{}} />)

    expect(screen.getByText('Agent A')).toBeInTheDocument()
    expect(screen.getByText('b')).toBeInTheDocument()
    expect(screen.getAllByText('Agent')).toHaveLength(1)
    expect(screen.getAllByText('Human')).toHaveLength(1)
  })

  it('suggests an orchestrator and a handoff agent for two adjacent agents with disjoint tooling', () => {
    const overlay = overlayOf([agentNode('a', 'Agent A', ['CRM']), agentNode('b', 'Agent B', ['ERP'])])
    render(<DigitalTwinPreview overlay={overlay} groups={computeAgentGroups(overlay, [])} labelsById={{}} />)

    expect(screen.getByText('Process Orchestrator Agent')).toBeInTheDocument()
    expect(screen.getByText('Handoff agent: Agent A → Agent B')).toBeInTheDocument()
  })

  it('does not suggest a handoff agent when adjacent agents share tooling', () => {
    const overlay = overlayOf([agentNode('a', 'Agent A', ['CRM']), agentNode('b', 'Agent B', ['CRM'])])
    render(<DigitalTwinPreview overlay={overlay} groups={computeAgentGroups(overlay, [])} labelsById={{}} />)

    expect(screen.queryByText(/handoff agent/i)).not.toBeInTheDocument()
  })

  it('suggests an exception-handling agent when an agent escalates on exception', () => {
    const overlay = overlayOf([agentNode('a', 'Agent A', ['CRM'], 'escalation_on_exception')])
    render(<DigitalTwinPreview overlay={overlay} groups={computeAgentGroups(overlay, [])} labelsById={{}} />)

    expect(screen.getByText('Exception Handling Agent')).toBeInTheDocument()
    expect(screen.getByText(/escalate exceptions straight to a human/i)).toBeInTheDocument()
  })

  it('shows a disclaimer that this is a hypothetical, not a verified simulation', () => {
    const overlay = overlayOf([agentNode('a', 'Agent A', [])])
    render(<DigitalTwinPreview overlay={overlay} groups={computeAgentGroups(overlay, [])} labelsById={{}} />)

    expect(screen.getByText(/not a verified simulation/i)).toBeInTheDocument()
  })
})
