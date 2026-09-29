import type { RunStatus } from '../api/types'
import { TERMINAL_RUN_STATUSES } from '../api/types'

// Epic 17: one shared status->badge-class mapping for both twin
// (DigitalTwinPanel.tsx) and orchestration (OrchestrationPanel.tsx) runs,
// replacing the two ad hoc per-file functions each used to have. `gradedPassed`
// is optional -- when known, a COMPLETED run whose scenario grading found
// deviations still reads as failed, since execution succeeding and grading
// agreeing are two different questions (see backend/app/schemas/run.py).
export function runStatusBadgeClass(status: RunStatus, gradedPassed?: boolean | null): string {
  if (status === 'COMPLETED') {
    return gradedPassed === false ? 'badge status-failed' : 'badge status-done'
  }
  if (isTerminalRunStatus(status)) return 'badge status-failed'
  return 'badge status-processing'
}

export function isTerminalRunStatus(status: RunStatus): boolean {
  return TERMINAL_RUN_STATUSES.has(status)
}
