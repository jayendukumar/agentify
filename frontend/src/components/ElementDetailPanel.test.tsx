import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ProcessSchema } from '../api/types'
import ElementDetailPanel from './ElementDetailPanel'

const schema: ProcessSchema = {
  process_name: 'Sample',
  actors: [{ id: 'actor-1', name: 'Support Agent', type: 'role' }],
  elements: [
    {
      id: 'el-1',
      type: 'task',
      label: 'Review Request',
      actor_id: 'actor-1',
      inputs: ['Request form'],
      outputs: ['Decision'],
      systems_touched: ['CRM'],
      source_refs: [{ document_id: 'doc-1', location: 'p.2', excerpt: 'review the request' }],
      confidence: 'high',
    },
  ],
  flows: [{ id: 'f-1', from: 'el-1', to: 'el-1', condition: 'approved' }],
}

describe('ElementDetailPanel', () => {
  it('shows a placeholder when nothing is selected', () => {
    render(<ElementDetailPanel elementId={null} schema={schema} />)
    expect(screen.getByText(/select a node or connection/i)).toBeInTheDocument()
  })

  it('renders element metadata for a matched task node', () => {
    render(<ElementDetailPanel elementId="Task_el-1" schema={schema} />)
    expect(screen.getByText('Review Request')).toBeInTheDocument()
    expect(screen.getByText(/Actor: Support Agent/)).toBeInTheDocument()
    expect(screen.getByText(/Inputs: Request form/)).toBeInTheDocument()
    expect(screen.getByText(/review the request/)).toBeInTheDocument()
  })

  it('renders a fallback for a manually added node with no schema match', () => {
    render(<ElementDetailPanel elementId="Task_manual-1" schema={schema} />)
    expect(screen.getByText(/manually added/i)).toBeInTheDocument()
  })

  it('renders flow metadata for a matched connection', () => {
    render(<ElementDetailPanel elementId="Flow_f-1" schema={schema} />)
    expect(screen.getByText(/Review Request.*Review Request/)).toBeInTheDocument()
    expect(screen.getByText(/Condition: approved/)).toBeInTheDocument()
  })

  it('renders actor metadata for a matched lane', () => {
    render(<ElementDetailPanel elementId="Lane_actor-1" schema={schema} />)
    expect(screen.getByText('Support Agent')).toBeInTheDocument()
  })
})
