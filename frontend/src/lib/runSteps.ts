import type { RunStep, StepType } from '../api/types'

// Epic 18, US18.3: Process/Agent/Technical are three filters/groupings over
// one shared RunStep[] (not three independently-fetched things) -- see
// RunDetailView.tsx for how selecting a step in any view drives the same
// inspector and canvas highlight.
export type RunView = 'process' | 'agent' | 'technical'

const AGENT_VIEW_TYPES: ReadonlySet<StepType> = new Set([
  'DECISION',
  'AGENT_DELEGATED',
  'RETRY',
  'ESCALATION',
  'POLICY_CHECK',
  'HUMAN_APPROVAL_REQUESTED',
  'HUMAN_APPROVED',
  'HUMAN_REJECTED',
])

/** Business-level phrasing for the Process view -- null means "too
 * technical to narrate" (MODEL_INVOKED, CONTEXT_RETRIEVED, POLICY_CHECK,
 * TOOL_REQUESTED, RETRY), so those steps are simply absent from that view
 * rather than shown as raw technical noise. */
export function narrateStep(step: RunStep): string | null {
  switch (step.type) {
    case 'RUN_STARTED':
      return 'Run started'
    case 'TOOL_EXECUTED':
      return `Looked up ${step.target ?? 'a system'}`
    case 'TOOL_FAILED':
      return step.denied ? `Blocked from calling ${step.target ?? 'a system'}` : `Failed calling ${step.target ?? 'a system'}`
    case 'HUMAN_APPROVAL_REQUESTED':
      return 'Asked a human to review'
    case 'HUMAN_APPROVED':
      return 'Approved by a human reviewer'
    case 'HUMAN_REJECTED':
      return 'Rejected by a human reviewer'
    case 'AGENT_DELEGATED':
      return `Handed off to ${step.target ?? 'the next agent'}`
    case 'DECISION':
      return 'Took a branch in the process'
    case 'ESCALATION':
      return `Escalated${step.reason ? `: ${step.reason}` : ''}`
    case 'FINAL_RESPONSE':
      return 'Produced the final result'
    case 'RUN_COMPLETED':
      return 'Run finished'
    default:
      return null
  }
}

export function stepsForView(steps: RunStep[], view: RunView): RunStep[] {
  if (view === 'technical') return steps
  if (view === 'agent') return steps.filter((s) => AGENT_VIEW_TYPES.has(s.type))
  return steps.filter((s) => narrateStep(s) !== null)
}

export interface CumulativeUsage {
  tokens: number
  costUsd: number | null
  elapsedMs: number
}

/** Running totals over steps[0..seq] (inclusive) -- for the replay
 * scrubber's "at this point" readout. `costUsd` is null the moment any
 * contributing step had usage but an unknown price, same "unknown beats a
 * wrong partial number" rule the backend's own cost accounting follows. */
export function cumulativeAt(steps: RunStep[], seq: number): CumulativeUsage {
  const relevant = steps.filter((s) => s.seq <= seq)
  let tokens = 0
  let costUsd = 0
  let costIncomplete = false
  for (const s of relevant) {
    if (s.tokens != null) tokens += s.tokens
    if (s.cost_usd != null) costUsd += s.cost_usd
    else if (s.tokens != null) costIncomplete = true
  }
  const first = steps[0]
  const last = relevant[relevant.length - 1]
  const elapsedMs = first && last ? new Date(last.occurred_at).getTime() - new Date(first.occurred_at).getTime() : 0
  return { tokens, costUsd: costIncomplete ? null : costUsd, elapsedMs }
}

/** How long `seq` took -- the gap until the next recorded step. Null for
 * the run's last step (nothing recorded after it to measure against). */
export function stepDurationMs(steps: RunStep[], seq: number): number | null {
  const index = steps.findIndex((s) => s.seq === seq)
  if (index < 0 || index === steps.length - 1) return null
  return new Date(steps[index + 1].occurred_at).getTime() - new Date(steps[index].occurred_at).getTime()
}
