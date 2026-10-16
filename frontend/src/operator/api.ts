// Epic 38, US38.8: the operator console's API client. Separate from the
// tenant app's (api/client.ts): a different cookie, no refresh token, and
// its own CSRF token.
import { API_BASE_URL, ApiError } from '../api/client'

export interface OperatorConfig {
  auth_mode: 'local' | 'oidc' | 'none'
  environment: string
  sso_login_url: string | null
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
}

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
  createTenant: (body: { slug: string; display_name: string; host: string; region: string | null }) =>
    call<Tenant>('/tenants', { method: 'POST', body: JSON.stringify(body) }),
  changeStatus: (tenantId: string, action: 'suspend' | 'activate' | 'delete') =>
    call<Tenant>(`/tenants/${tenantId}/${action}`, { method: 'POST' }),
  grants: (tenantId: string) => call<OperatorGrant[]>(`/tenants/${tenantId}/support-grants`),
  enter: (tenantId: string, grantId: string) =>
    call<{ url: string; expires_in_seconds: number }>(`/tenants/${tenantId}/support-grants/${grantId}/enter`, {
      method: 'POST',
    }),
  audit: () => call<PlatformEvent[]>('/audit'),
}
