// Epic 38, US38.8: the operator console's API client. Separate from the
// tenant app's (api/client.ts): a different cookie, no refresh token, and
// its own CSRF token.
import { API_BASE_URL, ApiError } from '../api/client'

export interface OperatorConfig {
  auth_mode: 'local' | 'oidc' | 'none'
  environment: string
  sso_login_url: string | null
  // US38.15: the region this deployment serves ('' for none), and every region's console.
  region: string
  regions: Record<string, string>
}

export interface Operator {
  id: string
  name: string
  email: string | null
  csrf_token: string
  session_expires_at: string
  auth_method: string
}

export interface TenantUsage {
  users: number
  processes: number
  jobs_waiting: number
  last_sign_in_at: string | null
  support_grants_active: number
  llm_calls_this_month: number
  llm_cost_this_month_usd: number
}

// US38.9: null is "the deployment's default" (as set) or "no limit" (in effect).
export interface TenantLimits {
  max_concurrent_jobs: number | null
  llm_requests_per_minute: number | null
  llm_monthly_budget_usd: number | null
}

// US38.12: how a tenant's first admin gets in.
export interface FirstAdmin {
  state: 'invited' | 'expired' | 'joined' | 'sso' | 'none'
  email: string | null
  expires_at: string | null
}

export interface TenantIdp {
  issuer: string
  discovery_url: string
  client_id: string
  client_secret_ref: string
  display_name: string
  acr_values: string
  mfa_acr_values: string[]
  admin_group: string
}

export interface TenantCreate {
  slug: string
  display_name: string
  host: string
  region: string | null
  first_admin_email?: string
  idp?: TenantIdp
}

export interface TenantCreated {
  tenant: Tenant
  invitation_url: string | null
  invitation_expires_at: string | null
}

export interface Tenant {
  id: string
  slug: string
  display_name: string
  status: 'active' | 'suspended' | 'deleting'
  region: string | null
  created_at: string
  hosts: string[]
  usage: TenantUsage | null
  limits: TenantLimits
  effective_limits: TenantLimits
  created_via: string
  first_admin: FirstAdmin | null
  // US38.13: suspended since; once deleted, the earliest time it may be erased.
  suspended_at: string | null
  delete_after: string | null
  last_export: LastExport | null
  // US38.14
  dedicated_database: boolean
}

export interface LastExport {
  status: string
  created_at: string
  downloaded_at: string | null
}

// US38.13: kept in the platform log when a tenant's data is erased.
export interface DeletionCertificate {
  id: string
  occurred_at: string
  operator_name: string
  tenant_id: string | null
  certificate: {
    slug: string
    erased_at: string
    rows_total: number
    files_removed: number
    tables: Record<string, number>
    secrets: Record<string, unknown>
    [key: string]: unknown
  }
}

export type StatusAction = 'suspend' | 'activate' | 'delete' | 'restore'

export interface OperatorGrant {
  id: string
  access: 'read' | 'write'
  reason: string
  granted_by: string
  expires_at: string
}

export interface PlatformEvent {
  id: string
  occurred_at: string
  operator_name: string
  action: string
  outcome: string
  tenant_id: string | null
  detail: Record<string, unknown>
}

let csrfToken: string | null = null

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (init.method && init.method !== 'GET' && csrfToken) headers.set('X-CSRF-Token', csrfToken)
  const response = await fetch(`${API_BASE_URL}/api/operator${path}`, { ...init, headers, credentials: 'include' })
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = ((await response.json()) as { detail?: string }).detail ?? detail
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, detail)
  }
  const body = (await response.json()) as T
  const token = (body as Partial<Operator> | null)?.csrf_token
  if (token) csrfToken = token
  return body
}

export const operatorApi = {
  config: () => call<OperatorConfig>('/config'),
  me: () => call<Operator>('/me'),
  login: (name: string, email: string) =>
    call<Operator>('/login', { method: 'POST', body: JSON.stringify({ name, email: email || null }) }),
  logout: () => call<{ logout_url: string | null }>('/logout', { method: 'POST' }),
  tenants: () => call<Tenant[]>('/tenants'),
  createTenant: (body: TenantCreate) => call<TenantCreated>('/tenants', { method: 'POST', body: JSON.stringify(body) }),
  invite: (tenantId: string, email: string) =>
    call<TenantCreated>(`/tenants/${tenantId}/invitation`, { method: 'POST', body: JSON.stringify({ email }) }),
  changeStatus: (tenantId: string, action: StatusAction) =>
    call<Tenant>(`/tenants/${tenantId}/${action}`, { method: 'POST' }),
  erase: (tenantId: string, confirmSlug: string) =>
    call<DeletionCertificate>(`/tenants/${tenantId}/erase`, {
      method: 'POST',
      body: JSON.stringify({ confirm_slug: confirmSlug }),
    }),
  certificates: () => call<DeletionCertificate[]>('/deletion-certificates'),
  setLimits: (tenantId: string, limits: TenantLimits) =>
    call<Tenant>(`/tenants/${tenantId}/limits`, { method: 'PUT', body: JSON.stringify(limits) }),
  grants: (tenantId: string) => call<OperatorGrant[]>(`/tenants/${tenantId}/support-grants`),
  enter: (tenantId: string, grantId: string) =>
    call<{ url: string; expires_in_seconds: number }>(`/tenants/${tenantId}/support-grants/${grantId}/enter`, {
      method: 'POST',
    }),
  audit: () => call<PlatformEvent[]>('/audit'),
}
