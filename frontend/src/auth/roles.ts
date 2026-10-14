import type { Role, RoleGrant } from '../api/types'

// Epic 37, US37.1: mirrors backend app/auth/permissions.py's ROLE_LABELS,
// with the one-line description the sign-in and Users pages show.
export const ROLE_INFO: { value: Role; label: string; description: string }[] = [
  { value: 'analyst', label: 'Process Analyst', description: 'Upload documents, edit diagrams, answer gaps and submit baselines.' },
  { value: 'architect', label: 'Automation Architect', description: 'Evaluate the blueprint, build and test agents, publish approved ones.' },
  { value: 'process_owner', label: 'Process Owner', description: 'Approve process baselines and confirm organization context.' },
  { value: 'approver', label: 'Approver', description: 'Approve agent releases built by someone else; set release criteria.' },
  { value: 'auditor', label: 'Auditor', description: 'Read everything, including the audit log.' },
  { value: 'admin', label: 'Admin', description: 'Manage users, roles and organization settings. Cannot build or approve.' },
]

export function roleLabel(role: Role): string {
  return ROLE_INFO.find((r) => r.value === role)?.label ?? role
}

export function grantLabel(grant: RoleGrant): string {
  const scope = grant.scope_type === 'business_unit' ? ` (${grant.scope_name ?? grant.scope_id})` : ''
  return `${roleLabel(grant.role)}${scope}`
}
