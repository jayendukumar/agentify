import type {
  EfficiencyReport,
  OperatingModel,
  OperatingModelView,
  ProcessTwinBatch,
  ProcessTwinEstimate,
  ProcessTwinRequest,
  AgentArtifact,
  BatchComparison,
  BatchResults,
  GateResult,
  ReleaseCriteria,
  BatchEstimate,
  BatchRequest,
  BatchSchedule,
  BatchTier,
  BatchView,
  DecisionRule,
  GenerateResult,
  JudgeRubric,
  MetamorphicRelation,
  OracleConflict,
  OracleLabelResult,
  ReviewItem,
  ColumnMapping,
  DatasetDiff,
  DatasetRow,
  DatasetRowInput,
  DatasetRowPage,
  DatasetSummary,
  ImportPreview,
  RowLabels,
  AccessLogEntry,
  Connector,
  ConnectorConfig,
  ConnectorRun,
  ContextChange,
  LlmPolicy,
  MemberAccess,
  Membership,
  SyncStarted,
  ComplianceEvidence,
  ApiSpecResult,
  ApplicationEndpoint,
  ApplicationImportResult,
  SystemResolution,
  ThirdPartyRegisterRow,
  ApplicableContext,
  ContextImpact,
  ContextPack,
  BulkConfirmResult,
  ContextDocument,
  ContextEntity,
  ContextEntityCreate,
  ContextEntityVersion,
  ContextKind,
  Organization,
  OrganizationProfileAnswers,
  PolicyPassage,
  ProcessScope,
  ProfileResult,
  StarterPackApplyResult,
  StarterPackSummary,
  AgentGovernanceUpdate,
  AgentPublication,
  AgentPublishStatus,
  ApiErrorBody,
  BlueprintOverlay,
  BlueprintVerdict,
  BPMNDocument,
  ChatMessageResult,
  DataMode,
  DocumentSummary,
  GeneratedRecord,
  GapFinding,
  GapFindingStatus,
  OrchestrationRun,
  OrchestrationScenario,
  OrchestrationScenarioCreate,
  ProcessDetail,
  ProcessSummary,
  RegistryEntry,
  RegistrySearchResult,
  RegistryStatus,
  RegressionSuite,
  RegressionSuiteCreate,
  RegressionSuiteRun,
  ReviewerPersona,
  Role,
  RoleGrant,
  ApprovalStatus,
  AuditEvent,
  AuditFilters,
  AuthConfig,
  LogoutResult,
  GovernanceSettings,
  RunComparison,
  GroupMapping,
  IdentitySettings,
  IdentitySettingsInput,
  ConnectionTest,
  HostKind,
  SupportGrant,
  SupportGrantInput,
  UsageOverview,
  ProviderKeyStatus,
  RegistrySetting,
  RegistrySettings,
  KnownGroup,
  RoleChange,
  ScimSetup,
  ScimTokenIssued,
  SessionInfo,
  SignedInUser,
  RunSnapshotView,
  Situation,
  TestBuilderContext,
  TwinBaseline,
  TwinEnvironment,
  TwinEnvironmentCreate,
  TwinRun,
  TwinScenario,
  TwinScenarioCreate,
  TwinSummary,
  User,
  VersionDetail,
  VersionDiffResult,
  VersionSummary,
} from './types'

// The session cookie (backend/app/api/auth.py) is SameSite=Lax with no
// explicit Domain, so it's only ever sent back on requests whose target
// host exactly matches whatever host issued it. Hardcoding this to
// 127.0.0.1 silently broke login for anyone who opened the frontend via
// http://localhost:3000 instead (start.ps1's own printed URL) -- same
// page, "different site" as far as the browser's cookie jar is concerned,
// so every post-login request came back 401 and bounced straight back to
// the login screen. Defaulting to the page's own hostname keeps the API
// on the same site as the frontend regardless of which of the two the
// browser was actually pointed at.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? `${window.location.protocol}//${window.location.hostname}:8000`

export class ApiError extends Error {
  status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

// Any request can come back 401 mid-session -- the session cookie only
// lasts 15 minutes (Epic 37, US37.12), and the session can be revoked or
// time out. apiFetch first swaps the refresh cookie for new ones and
// retries; only if that fails does AuthContext's listener drop the user
// back to the login screen, instead of every call site having to notice
// and redirect itself.
type UnauthorizedListener = () => void
let unauthorizedListener: UnauthorizedListener | null = null

export function onUnauthorized(listener: UnauthorizedListener): void {
  unauthorizedListener = listener
}

async function readErrorDetail(response: Response): Promise<string> {
  let detail = response.statusText
  try {
    const body = (await response.json()) as ApiErrorBody
    detail = body.detail ?? detail
  } catch {
    // response body wasn't JSON -- fall back to statusText
  }
  return detail
}

// Epic 37, US37.12: every write carries the session's CSRF token in a
// header, since cookies are the only credential. The token comes back in
// the body of sign-in, refresh and /api/auth/me -- not in a cookie, which
// script on :3000 couldn't read if the API moved to another host.
const CSRF_HEADER = 'X-CSRF-Token'
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])
let csrfToken: string | null = null

function rememberSession(body: unknown): void {
  const token = (body as Partial<SignedInUser> | null)?.csrf_token
  if (token) csrfToken = token
}

// One refresh at a time: a page firing several requests that all 401 at
// once would otherwise present the same refresh token several times, and
// the server treats a reused refresh token as theft.
let refreshing: Promise<boolean> | null = null

function refreshSession(): Promise<boolean> {
  refreshing ??= fetch(`${API_BASE_URL}/api/auth/refresh`, { method: 'POST', credentials: 'include' })
    .then(async (response) => {
      if (!response.ok) return false
      rememberSession(await response.json())
      return true
    })
    .catch(() => false)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

const NO_RETRY = new Set(['/api/auth/login', '/api/auth/refresh'])

async function apiFetch(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  const headers = new Headers(init.headers)
  if (!SAFE_METHODS.has((init.method ?? 'GET').toUpperCase()) && csrfToken) headers.set(CSRF_HEADER, csrfToken)
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers,
    // Epic 9/10, US9.9: the session cookie lives on the API's origin
    // (:8000), distinct from the frontend's (:3000) -- without this, the
    // browser never sends/stores it and every request looks logged-out.
    credentials: 'include',
  })
  if (retried || NO_RETRY.has(path)) return response
  if (response.status === 401 && (await refreshSession())) return apiFetch(path, init, true)
  // Signed in again in another tab: this tab's token is for the old session.
  if (response.status === 403 && response.headers.get('X-Auth-Error') === 'csrf') {
    const me = await apiFetch('/api/auth/me')
    if (me.ok) {
      rememberSession(await me.json())
      return apiFetch(path, init, true)
    }
  }
  return response
}

async function failure(response: Response): Promise<ApiError> {
  const detail = await readErrorDetail(response)
  if (response.status === 401) unauthorizedListener?.()
  return new ApiError(response.status, detail)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })

  if (!response.ok) throw await failure(response)

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export async function login(name: string, roles: Role[] = [], businessUnitId?: string | null): Promise<SignedInUser> {
  const user = await request<SignedInUser>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ name, roles, business_unit_id: businessUnitId || null }),
  })
  rememberSession(user)
  return user
}

export function getAuthConfig(): Promise<AuthConfig> {
  return request('/api/auth/config')
}

// -- Epic 37: approvals, users, audit, approval policy --------------------------

export function getVersionApproval(processId: string, versionId: string): Promise<ApprovalStatus> {
  return request(`/api/processes/${processId}/versions/${versionId}/approval`)
}

export function approveVersion(processId: string, versionId: string, comment?: string): Promise<VersionSummary> {
  return request(`/api/processes/${processId}/versions/${versionId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ comment: comment || null }),
  })
}

export function rejectVersion(processId: string, versionId: string, comment?: string): Promise<VersionSummary> {
  return request(`/api/processes/${processId}/versions/${versionId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ comment: comment || null }),
  })
}

export function getAgentApproval(processId: string, artifactId: string): Promise<ApprovalStatus> {
  return request(`/api/processes/${processId}/blueprint/agent-artifacts/${artifactId}/approval`)
}

export function listUsers(): Promise<User[]> {
  return request('/api/users')
}

export function setUserRoles(userId: string, roles: RoleGrant[]): Promise<User> {
  return request(`/api/users/${userId}/roles`, { method: 'PUT', body: JSON.stringify({ roles }) })
}

export function setUserActive(userId: string, active: boolean): Promise<User> {
  return request(`/api/users/${userId}/${active ? 'activate' : 'deactivate'}`, { method: 'POST' })
}

// Epic 37, US37.10-37.11: SCIM tokens and group-to-role mappings.
export function getIdentitySettings(): Promise<IdentitySettings> {
  return request('/api/directory/identity')
}

export function testIdentitySettings(settings: IdentitySettingsInput): Promise<ConnectionTest> {
  return request('/api/directory/identity/test', { method: 'POST', body: JSON.stringify(settings) })
}

export function saveIdentitySettings(settings: IdentitySettingsInput): Promise<IdentitySettings> {
  return request('/api/directory/identity', { method: 'PUT', body: JSON.stringify(settings) })
}

// Epic 38, US38.8: support access for the hosting company.
export function listSupportGrants(): Promise<SupportGrant[]> {
  return request('/api/directory/support-grants')
}

export function grantSupport(grant: SupportGrantInput): Promise<SupportGrant> {
  return request('/api/directory/support-grants', { method: 'POST', body: JSON.stringify(grant) })
}

export function revokeSupport(grantId: string): Promise<SupportGrant> {
  return request(`/api/directory/support-grants/${grantId}/revoke`, { method: 'POST' })
}

// Epic 38, US38.11: this organization's registries and model provider key.
export function getRegistrySettings(): Promise<RegistrySettings> {
  return request('/api/organization/registries')
}

export function saveRegistrySettings(registries: RegistrySetting[]): Promise<RegistrySettings> {
  return request('/api/organization/registries', { method: 'PUT', body: JSON.stringify({ registries }) })
}

export function getProviderKey(): Promise<ProviderKeyStatus> {
  return request('/api/organization/llm-provider-key')
}

export function setProviderKey(keyRef: string | null): Promise<ProviderKeyStatus> {
  return request('/api/organization/llm-provider-key', { method: 'PUT', body: JSON.stringify({ key_ref: keyRef }) })
}

// Epic 38, US38.9: this organization's LLM usage and cost per month, and its limits.
export function getUsage(): Promise<UsageOverview> {
  return request('/api/usage')
}

// Which app this host serves: a tenant's, or the operator console (US38.8).
export async function getHostKind(): Promise<HostKind> {
  const response = await fetch(`${API_BASE_URL}/api/host`, { credentials: 'include' })
  if (!response.ok) throw new ApiError(response.status, response.statusText)
  return response.json()
}

export function getScimSetup(): Promise<ScimSetup> {
  return request('/api/directory/scim')
}

export function createScimToken(label: string): Promise<ScimTokenIssued> {
  return request('/api/directory/scim/tokens', { method: 'POST', body: JSON.stringify({ label }) })
}

export function revokeScimToken(tokenId: string): Promise<void> {
  return request(`/api/directory/scim/tokens/${tokenId}`, { method: 'DELETE' })
}

export function listKnownGroups(): Promise<KnownGroup[]> {
  return request('/api/directory/groups')
}

export function listGroupMappings(): Promise<GroupMapping[]> {
  return request('/api/directory/group-mappings')
}

export function previewGroupMappings(mappings: GroupMapping[]): Promise<RoleChange[]> {
  return request('/api/directory/group-mappings/preview', { method: 'POST', body: JSON.stringify({ mappings }) })
}

export function saveGroupMappings(mappings: GroupMapping[]): Promise<{ mappings: GroupMapping[]; changes: RoleChange[] }> {
  return request('/api/directory/group-mappings', { method: 'PUT', body: JSON.stringify({ mappings }) })
}

// Epic 37, US37.13: where someone is signed in, and ending it.
export function listMySessions(): Promise<SessionInfo[]> {
  return request('/api/auth/sessions')
}

export function endMySession(sessionId: string): Promise<void> {
  return request(`/api/auth/sessions/${sessionId}`, { method: 'DELETE' })
}

export function listUserSessions(userId: string): Promise<SessionInfo[]> {
  return request(`/api/users/${userId}/sessions`)
}

export function revokeUserSession(userId: string, sessionId: string): Promise<void> {
  return request(`/api/users/${userId}/sessions/${sessionId}`, { method: 'DELETE' })
}

export function revokeAllUserSessions(userId: string): Promise<{ revoked: number }> {
  return request(`/api/users/${userId}/sessions/revoke-all`, { method: 'POST' })
}

function auditQuery(filters: AuditFilters): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
  const query = params.toString()
  return query ? `?${query}` : ''
}

export function listAuditEvents(filters: AuditFilters = {}): Promise<AuditEvent[]> {
  return request(`/api/audit-events${auditQuery(filters)}`)
}

export function auditExportUrl(filters: AuditFilters = {}): string {
  return `${API_BASE_URL}/api/audit-events/export${auditQuery(filters)}`
}

export function getGovernanceSettings(): Promise<GovernanceSettings> {
  return request('/api/organization/governance')
}

export function setGovernanceSettings(settings: GovernanceSettings): Promise<GovernanceSettings> {
  return request('/api/organization/governance', { method: 'PUT', body: JSON.stringify(settings) })
}

export async function logout(): Promise<LogoutResult> {
  const result = await request<LogoutResult>('/api/auth/logout', { method: 'POST' })
  csrfToken = null
  return result
}

// Epic 37, US37.7: a full-page navigation, not a fetch -- the API sends the
// browser on to the IdP, which sends it back to the API's callback.
export function ssoLoginUrl(loginPath: string, returnTo: string): string {
  return `${API_BASE_URL}${loginPath}?${new URLSearchParams({ return_to: returnTo })}`
}

export async function getCurrentUser(): Promise<SignedInUser | null> {
  try {
    const user = await request<SignedInUser>('/api/auth/me')
    rememberSession(user)
    return user
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null
    throw err
  }
}

export function listProcesses(): Promise<ProcessSummary[]> {
  return request('/api/processes')
}

export function createProcess(name: string): Promise<ProcessSummary> {
  return request('/api/processes', { method: 'POST', body: JSON.stringify({ name }) })
}

export function getProcess(processId: string): Promise<ProcessDetail> {
  return request(`/api/processes/${processId}`)
}

export function deleteProcess(processId: string): Promise<void> {
  return request(`/api/processes/${processId}`, { method: 'DELETE' })
}

export function listDocuments(processId: string): Promise<DocumentSummary[]> {
  return request(`/api/processes/${processId}/documents`)
}

export function deleteDocument(processId: string, documentId: string): Promise<void> {
  return request(`/api/processes/${processId}/documents/${documentId}`, { method: 'DELETE' })
}

export function generateBpmn(processId: string): Promise<BPMNDocument> {
  return request(`/api/processes/${processId}/bpmn/generate`, { method: 'POST', body: JSON.stringify({}) })
}

export async function getBlueprint(processId: string): Promise<BlueprintOverlay | null> {
  try {
    return await request<BlueprintOverlay>(`/api/processes/${processId}/blueprint`)
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export function generateBlueprint(processId: string, versionId?: string | null): Promise<BlueprintOverlay> {
  return request(`/api/processes/${processId}/blueprint/generate`, {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId ?? null }),
  })
}

export function overrideBlueprintNode(
  processId: string,
  nodeId: string,
  verdict: BlueprintVerdict,
  justification: string,
): Promise<BlueprintOverlay> {
  return request(`/api/processes/${processId}/blueprint/nodes/${nodeId}`, {
    method: 'PATCH',
    body: JSON.stringify({ verdict, justification }),
  })
}

// Epic 12
export function generateAgentArtifact(processId: string, nodeId: string): Promise<AgentArtifact> {
  return request(`/api/processes/${processId}/blueprint/nodes/${nodeId}/agent-artifact`, { method: 'POST' })
}

export function listAgentArtifacts(processId: string): Promise<AgentArtifact[]> {
  return request(`/api/processes/${processId}/blueprint/agent-artifacts`)
}

// Epic 20
export function updateAgentGovernance(
  processId: string,
  artifactId: string,
  update: AgentGovernanceUpdate,
): Promise<AgentArtifact> {
  return request(`/api/processes/${processId}/blueprint/agent-artifacts/${artifactId}/governance`, {
    method: 'PATCH',
    body: JSON.stringify(update),
  })
}

// Epic 23
export function markAgentReleaseCandidate(processId: string, artifactId: string): Promise<AgentArtifact> {
  return request(`/api/processes/${processId}/blueprint/agent-artifacts/${artifactId}/mark-release-candidate`, {
    method: 'POST',
  })
}

export function approveAgentArtifact(processId: string, artifactId: string, comment?: string): Promise<AgentArtifact> {
  return request(`/api/processes/${processId}/blueprint/agent-artifacts/${artifactId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ comment: comment || null }),
  })
}

// Not JSON (returns text/markdown), so this bypasses the request() helper.
export async function exportBlueprint(processId: string): Promise<string> {
  const response = await apiFetch(`/api/processes/${processId}/blueprint/export?format=markdown`)
  if (!response.ok) throw await failure(response)
  return response.text()
}

export async function getBpmn(processId: string): Promise<BPMNDocument | null> {
  try {
    return await request<BPMNDocument>(`/api/processes/${processId}/bpmn`)
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export function updateBpmn(processId: string, xml: string): Promise<BPMNDocument> {
  return request(`/api/processes/${processId}/bpmn`, { method: 'PUT', body: JSON.stringify({ xml }) })
}

export function listChatMessages(processId: string): Promise<ChatMessageResult[]> {
  return request(`/api/processes/${processId}/chat/messages`)
}

export function sendChatMessage(
  processId: string,
  text: string,
  selectedElementId?: string | null,
): Promise<ChatMessageResult> {
  return request(`/api/processes/${processId}/chat/messages`, {
    method: 'POST',
    body: JSON.stringify({ text, selected_element_id: selectedElementId ?? null }),
  })
}

export function applyChatMessage(processId: string, messageId: string, confirm: boolean): Promise<ChatMessageResult> {
  return request(`/api/processes/${processId}/chat/messages/${messageId}/apply`, {
    method: 'POST',
    body: JSON.stringify({ confirm }),
  })
}

export function finalizeProcess(processId: string): Promise<VersionSummary> {
  return request(`/api/processes/${processId}/finalize`, { method: 'POST' })
}

export function listVersions(processId: string): Promise<VersionSummary[]> {
  return request(`/api/processes/${processId}/versions`)
}

export function getVersion(processId: string, versionId: string): Promise<VersionDetail> {
  return request(`/api/processes/${processId}/versions/${versionId}`)
}

export function restoreVersion(processId: string, versionId: string): Promise<VersionSummary> {
  return request(`/api/processes/${processId}/versions/${versionId}/restore`, { method: 'POST' })
}

export function diffVersions(
  processId: string,
  fromVersionId: string,
  toVersionId: string,
): Promise<VersionDiffResult> {
  const params = new URLSearchParams({ from_version_id: fromVersionId, to_version_id: toVersionId })
  return request(`/api/processes/${processId}/versions/diff?${params.toString()}`)
}

export function listGapFindings(processId: string, status?: GapFindingStatus): Promise<GapFinding[]> {
  const query = status ? `?${new URLSearchParams({ status }).toString()}` : ''
  return request(`/api/processes/${processId}/gap-findings${query}`)
}

export function analyzeGaps(processId: string): Promise<GapFinding[]> {
  return request(`/api/processes/${processId}/gap-findings/analyze`, { method: 'POST' })
}

export function resolveGapFinding(processId: string, findingId: string, optionIndex: number): Promise<GapFinding> {
  return request(`/api/processes/${processId}/gap-findings/${findingId}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ option_index: optionIndex }),
  })
}

export function dismissGapFinding(processId: string, findingId: string): Promise<GapFinding> {
  return request(`/api/processes/${processId}/gap-findings/${findingId}/dismiss`, { method: 'POST' })
}

export async function uploadDocuments(processId: string, files: FileList | File[]): Promise<DocumentSummary[]> {
  const formData = new FormData()
  for (const file of Array.from(files)) {
    formData.append('files', file)
  }

  const response = await apiFetch(`/api/processes/${processId}/documents`, { method: 'POST', body: formData })
  if (!response.ok) throw await failure(response)

  return (await response.json()) as DocumentSummary[]
}

// Epic 13
export function listRegistries(): Promise<RegistryStatus[]> {
  return request('/api/registries')
}

export function searchRegistries(query: string): Promise<RegistrySearchResult> {
  const params = query ? `?${new URLSearchParams({ q: query }).toString()}` : ''
  return request(`/api/registries/search${params}`)
}

export function pushToRegistry(
  registryName: string,
  body: {
    agent_name: string
    definition: Record<string, unknown>
    tags?: string[]
    source_process_id?: string | null
    source_node_ids?: string[]
  },
): Promise<RegistryEntry> {
  return request(`/api/registries/${registryName}/push`, { method: 'POST', body: JSON.stringify(body) })
}

// Epic 14 (core slice)
export function listScenarios(processId: string, artifactId: string): Promise<TwinScenario[]> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/scenarios`)
}

export function createScenario(processId: string, artifactId: string, body: TwinScenarioCreate): Promise<TwinScenario> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/scenarios`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// Epics 24/25: the guided test builder. `getTestBuilderContext` is a
// deterministic read; the rest are paid LLM drafting calls that persist
// nothing -- confirmed situations are saved through `createScenario`,
// generated data/personas through the environment endpoints.
function testBuilderPath(processId: string, artifactId: string, suffix: string): string {
  return `/api/processes/${processId}/agent-artifacts/${artifactId}/test-builder/${suffix}`
}

export function getTestBuilderContext(processId: string, artifactId: string): Promise<TestBuilderContext> {
  return request(testBuilderPath(processId, artifactId, 'context'))
}

export async function suggestSituations(
  processId: string,
  artifactId: string,
  body: { count?: number; focus?: string | null; already_covered?: string[] },
): Promise<Situation[]> {
  const result = await request<{ situations: Situation[] }>(testBuilderPath(processId, artifactId, 'suggest'), {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return result.situations
}

export function describeSituation(processId: string, artifactId: string, description: string): Promise<Situation> {
  return request(testBuilderPath(processId, artifactId, 'describe'), {
    method: 'POST',
    body: JSON.stringify({ description }),
  })
}

export async function generateTestData(
  processId: string,
  artifactId: string,
  body: {
    mode: DataMode
    count?: number
    prompt?: string | null
    seed_record?: Record<string, unknown> | null
    sample_text?: string | null
  },
): Promise<GeneratedRecord[]> {
  const result = await request<{ records: GeneratedRecord[] }>(testBuilderPath(processId, artifactId, 'data'), {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return result.records
}

export async function generateReviewerPersonas(
  processId: string,
  artifactId: string,
  body: { count?: number; prompt?: string | null },
): Promise<ReviewerPersona[]> {
  const result = await request<{ personas: ReviewerPersona[] }>(testBuilderPath(processId, artifactId, 'personas'), {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return result.personas
}

export function deleteScenario(processId: string, scenarioId: string): Promise<void> {
  return request(`/api/processes/${processId}/scenarios/${scenarioId}`, { method: 'DELETE' })
}

// Epic 17, US17.6: twin runs now execute in the background -- this returns
// immediately with the run in status="CREATED"; poll `getTwinRun` for
// progress, same pattern as `startOrchestrationRun`/`getOrchestrationRun`.
export function startTwinRun(processId: string, scenarioId: string, rowKey?: string): Promise<TwinRun> {
  const query = rowKey ? `?row_key=${encodeURIComponent(rowKey)}` : ''
  return request(`/api/processes/${processId}/scenarios/${scenarioId}/run${query}`, { method: 'POST' })
}

export function getTwinRun(processId: string, runId: string): Promise<TwinRun> {
  return request(`/api/processes/${processId}/twin-runs/${runId}`)
}

export function cancelTwinRun(processId: string, runId: string): Promise<void> {
  return request(`/api/processes/${processId}/twin-runs/${runId}/cancel`, { method: 'POST' })
}

export function resumeTwinRun(processId: string, runId: string, decision: 'approve' | 'reject'): Promise<TwinRun> {
  return request(`/api/processes/${processId}/twin-runs/${runId}/resume`, {
    method: 'POST',
    body: JSON.stringify({ decision }),
  })
}

export function listTwinRuns(processId: string, artifactId: string): Promise<TwinRun[]> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/runs`)
}

export function getTwinSummary(processId: string, artifactId: string): Promise<TwinSummary> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/twin-summary`)
}

export function setTwinBaseline(
  processId: string,
  artifactId: string,
  body: { typical_time_seconds?: number | null; error_rate?: number | null; notes?: string | null },
): Promise<TwinBaseline> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/baseline`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deleteTwinBaseline(processId: string, artifactId: string): Promise<void> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/baseline`, { method: 'DELETE' })
}

// Epic 16
export function listOrchestrationScenarios(processId: string): Promise<OrchestrationScenario[]> {
  return request(`/api/processes/${processId}/orchestration/scenarios`)
}

export function createOrchestrationScenario(
  processId: string,
  body: OrchestrationScenarioCreate,
): Promise<OrchestrationScenario> {
  return request(`/api/processes/${processId}/orchestration/scenarios`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function deleteOrchestrationScenario(processId: string, scenarioId: string): Promise<void> {
  return request(`/api/processes/${processId}/orchestration/scenarios/${scenarioId}`, { method: 'DELETE' })
}

export function startOrchestrationRun(processId: string, scenarioId: string): Promise<OrchestrationRun> {
  return request(`/api/processes/${processId}/orchestration/scenarios/${scenarioId}/run`, { method: 'POST' })
}

export function getOrchestrationRun(processId: string, runId: string): Promise<OrchestrationRun> {
  return request(`/api/processes/${processId}/orchestration/runs/${runId}`)
}

export function cancelOrchestrationRun(processId: string, runId: string): Promise<void> {
  return request(`/api/processes/${processId}/orchestration/runs/${runId}/cancel`, { method: 'POST' })
}

export function resumeOrchestrationRun(
  processId: string,
  runId: string,
  decision: 'approve' | 'reject',
): Promise<OrchestrationRun> {
  return request(`/api/processes/${processId}/orchestration/runs/${runId}/resume`, {
    method: 'POST',
    body: JSON.stringify({ decision }),
  })
}

export function listOrchestrationRuns(processId: string, scenarioId?: string): Promise<OrchestrationRun[]> {
  const query = scenarioId ? `?scenario_id=${encodeURIComponent(scenarioId)}` : ''
  return request(`/api/processes/${processId}/orchestration/runs${query}`)
}

// Epic 15
export function getPublishStatus(processId: string, artifactId: string): Promise<AgentPublishStatus> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/publish-status`)
}

export function publishAgentArtifact(
  processId: string,
  artifactId: string,
  registryName: string,
): Promise<AgentPublication> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/publish`, {
    method: 'POST',
    body: JSON.stringify({ registry_name: registryName }),
  })
}

export function markPublicationDeployed(
  processId: string,
  artifactId: string,
  publicationId: string,
): Promise<AgentPublication> {
  return request(
    `/api/processes/${processId}/agent-artifacts/${artifactId}/publications/${publicationId}/mark-deployed`,
    { method: 'POST' },
  )
}

// Epic 19: regression suites (US19.2/US19.3) and run comparison (US19.4)
export function listRegressionSuites(processId: string): Promise<RegressionSuite[]> {
  return request(`/api/processes/${processId}/regression-suites`)
}

export function createRegressionSuite(processId: string, body: RegressionSuiteCreate): Promise<RegressionSuite> {
  return request(`/api/processes/${processId}/regression-suites`, { method: 'POST', body: JSON.stringify(body) })
}

export function deleteRegressionSuite(processId: string, suiteId: string): Promise<void> {
  return request(`/api/processes/${processId}/regression-suites/${suiteId}`, { method: 'DELETE' })
}

export function startRegressionSuiteRun(processId: string, suiteId: string): Promise<RegressionSuiteRun> {
  return request(`/api/processes/${processId}/regression-suites/${suiteId}/run`, { method: 'POST' })
}

export function getRegressionSuiteRun(processId: string, suiteRunId: string): Promise<RegressionSuiteRun> {
  return request(`/api/processes/${processId}/regression-suite-runs/${suiteRunId}`)
}

export function compareRuns(processId: string, runAId: string, runBId: string): Promise<RunComparison> {
  return request(
    `/api/processes/${processId}/runs/compare?run_a=${encodeURIComponent(runAId)}&run_b=${encodeURIComponent(runBId)}`,
  )
}

// Epic 21: Digital Twin Environments and their fault-injection catalog
export function listTwinEnvironments(processId: string): Promise<TwinEnvironment[]> {
  return request(`/api/processes/${processId}/twin-environments`)
}

export function createTwinEnvironment(processId: string, body: TwinEnvironmentCreate): Promise<TwinEnvironment> {
  return request(`/api/processes/${processId}/twin-environments`, { method: 'POST', body: JSON.stringify(body) })
}

export function updateTwinEnvironment(
  processId: string,
  environmentId: string,
  body: TwinEnvironmentCreate,
): Promise<TwinEnvironment> {
  return request(`/api/processes/${processId}/twin-environments/${environmentId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function deleteTwinEnvironment(processId: string, environmentId: string): Promise<void> {
  return request(`/api/processes/${processId}/twin-environments/${environmentId}`, { method: 'DELETE' })
}

// Epic 22: per-run execution snapshots and reproducibility tiers
export function getRunSnapshot(processId: string, runId: string): Promise<RunSnapshotView> {
  return request(`/api/processes/${processId}/runs/${runId}/snapshot`)
}

// Epic 27 -- organization context
export function getOrganization(): Promise<Organization> {
  return request('/api/organization')
}

export function updateOrganization(body: { name: string; industry: string | null; size: string | null }): Promise<Organization> {
  return request('/api/organization', { method: 'PUT', body: JSON.stringify(body) })
}

export function applyOrganizationProfile(answers: OrganizationProfileAnswers): Promise<ProfileResult> {
  return request('/api/organization/profile', { method: 'POST', body: JSON.stringify(answers) })
}

export function listStarterPacks(): Promise<StarterPackSummary[]> {
  return request('/api/organization/starter-packs')
}

export function applyStarterPack(packId: string): Promise<StarterPackApplyResult> {
  return request(`/api/organization/starter-packs/${packId}/apply`, { method: 'POST' })
}

export function listContextEntities(kind?: ContextKind | null, status: string = 'confirmed'): Promise<ContextEntity[]> {
  const params = new URLSearchParams({ status })
  if (kind) params.set('kind', kind)
  return request(`/api/organization/entities?${params.toString()}`)
}

export function createContextEntity(body: ContextEntityCreate): Promise<ContextEntity> {
  return request('/api/organization/entities', { method: 'POST', body: JSON.stringify(body) })
}

export function updateContextEntity(
  entityId: string,
  body: Partial<ContextEntityCreate> & { change_note?: string },
): Promise<ContextEntity> {
  return request(`/api/organization/entities/${entityId}`, { method: 'PUT', body: JSON.stringify(body) })
}

export function retireContextEntity(entityId: string): Promise<ContextEntity> {
  return request(`/api/organization/entities/${entityId}`, { method: 'DELETE' })
}

export function listContextEntityVersions(entityId: string): Promise<ContextEntityVersion[]> {
  return request(`/api/organization/entities/${entityId}/versions`)
}

export function confirmContextEntity(entityId: string, acknowledgeRelaxation = false): Promise<ContextEntity> {
  return request(`/api/organization/entities/${entityId}/confirm`, {
    method: 'POST',
    body: JSON.stringify({ acknowledge_relaxation: acknowledgeRelaxation }),
  })
}

export function dismissContextEntity(entityId: string): Promise<ContextEntity> {
  return request(`/api/organization/entities/${entityId}/dismiss`, { method: 'POST' })
}

export function bulkConfirmContextEntities(ids?: string[]): Promise<BulkConfirmResult> {
  return request('/api/organization/entities/bulk-confirm', { method: 'POST', body: JSON.stringify(ids ? { ids } : {}) })
}

export async function uploadPolicyDocument(
  file: File,
  fields: { title: string; code: string; business_unit_id: string; jurisdictions: string; effective_date: string },
): Promise<ContextDocument> {
  const formData = new FormData()
  formData.append('file', file)
  for (const [key, value] of Object.entries(fields)) formData.append(key, value)
  const response = await apiFetch('/api/organization/documents', { method: 'POST', body: formData })
  if (!response.ok) throw await failure(response)
  return (await response.json()) as ContextDocument
}

export function listPolicyDocuments(): Promise<ContextDocument[]> {
  return request('/api/organization/documents')
}

export function listPolicyPassages(documentId: string): Promise<PolicyPassage[]> {
  return request(`/api/organization/documents/${documentId}/passages`)
}

export function getApplicableContext(scope: Partial<ProcessScope>): Promise<ApplicableContext> {
  const params = new URLSearchParams()
  if (scope.business_unit_id) params.set('business_unit_id', scope.business_unit_id)
  if (scope.jurisdictions?.length) params.set('jurisdictions', scope.jurisdictions.join(','))
  if (scope.process_type) params.set('process_type', scope.process_type)
  if (scope.data_classes?.length) params.set('data_classes', scope.data_classes.join(','))
  return request(`/api/organization/applicable?${params.toString()}`)
}

export function getProcessScope(processId: string): Promise<ProcessScope> {
  return request(`/api/processes/${processId}/scope`)
}

export function setProcessScope(processId: string, scope: ProcessScope): Promise<ProcessScope> {
  return request(`/api/processes/${processId}/scope`, { method: 'PUT', body: JSON.stringify(scope) })
}

// Epic 28 -- context packs
export function getContextPack(packId: string): Promise<ContextPack> {
  return request(`/api/context-packs/${packId}`)
}

export function previewContextPack(processId: string, purpose: string, query = ''): Promise<ContextPack> {
  const params = new URLSearchParams({ purpose, query })
  return request(`/api/processes/${processId}/scope/context-pack?${params.toString()}`)
}

export function getContextImpact(entityId: string): Promise<ContextImpact> {
  return request(`/api/organization/entities/${entityId}/impact`)
}

export function rerunAffectedSuites(entityId: string): Promise<ContextImpact> {
  return request(`/api/organization/entities/${entityId}/rerun-affected`, { method: 'POST' })
}

// Epic 29 -- application and data catalog
async function uploadForm<T>(path: string, method: string, form: FormData): Promise<T> {
  const response = await apiFetch(path, { method, body: form })
  if (!response.ok) throw await failure(response)
  return (await response.json()) as T
}

export function importApplicationsCsv(file: File, dryRun: boolean): Promise<ApplicationImportResult> {
  const form = new FormData()
  form.append('file', file)
  return uploadForm(`/api/organization/applications/import?dry_run=${dryRun}`, 'POST', form)
}

export function attachApiSpec(applicationId: string, file: File): Promise<ApiSpecResult> {
  const form = new FormData()
  form.append('file', file)
  return uploadForm(`/api/organization/applications/${applicationId}/api-spec`, 'PUT', form)
}

export function setApplicationEndpoints(applicationId: string, endpoints: ApplicationEndpoint[]): Promise<ContextEntity> {
  return request(`/api/organization/applications/${applicationId}/endpoints`, { method: 'PUT', body: JSON.stringify(endpoints) })
}

export function getThirdPartyRegister(): Promise<ThirdPartyRegisterRow[]> {
  return request('/api/organization/applications/register')
}

export function listSystemResolutions(processId: string): Promise<SystemResolution[]> {
  return request(`/api/processes/${processId}/systems`)
}

export function linkSystem(processId: string, systemName: string, applicationId: string | null): Promise<SystemResolution[]> {
  return request(`/api/processes/${processId}/systems/${encodeURIComponent(systemName)}`, {
    method: 'PUT',
    body: JSON.stringify({ application_id: applicationId }),
  })
}

// Epic 30 -- compliance-aware testing and release
export async function getAuthorityPersonas(processId: string, artifactId: string): Promise<ReviewerPersona[]> {
  const result = await request<{ personas: ReviewerPersona[] }>(testBuilderPath(processId, artifactId, 'authority-personas'))
  return result.personas
}

export function getComplianceEvidence(processId: string, artifactId: string): Promise<ComplianceEvidence> {
  return request(`/api/processes/${processId}/blueprint/agent-artifacts/${artifactId}/compliance-evidence`)
}

export async function exportComplianceEvidence(processId: string, artifactId: string): Promise<string> {
  const response = await apiFetch(
    `/api/processes/${processId}/blueprint/agent-artifacts/${artifactId}/compliance-evidence/export`,
  )
  if (!response.ok) throw await failure(response)
  return response.text()
}

// Epic 31 -- connectors, federation, model policy, audit
export function listConnectorTypes(): Promise<{ type: string; description: string }[]> {
  return request('/api/organization/connectors/types')
}

export function listConnectors(): Promise<Connector[]> {
  return request('/api/organization/connectors')
}

export function createConnector(body: ConnectorConfig): Promise<Connector> {
  return request('/api/organization/connectors', { method: 'POST', body: JSON.stringify(body) })
}

export function deleteConnector(connectorId: string): Promise<void> {
  return request(`/api/organization/connectors/${connectorId}`, { method: 'DELETE' })
}

export function syncConnector(connectorId: string): Promise<SyncStarted> {
  return request(`/api/organization/connectors/${connectorId}/sync`, { method: 'POST' })
}

export function listConnectorRuns(connectorId: string): Promise<ConnectorRun[]> {
  return request(`/api/organization/connectors/${connectorId}/runs`)
}

export function listMembers(): Promise<MemberAccess[]> {
  return request('/api/organization/members')
}

export function setMemberships(userId: string, memberships: Membership[]): Promise<MemberAccess> {
  return request(`/api/organization/members/${userId}`, { method: 'PUT', body: JSON.stringify(memberships) })
}

export function setOrganizationLlmPolicy(policy: LlmPolicy): Promise<Organization> {
  return request('/api/organization/llm-policy', { method: 'PUT', body: JSON.stringify(policy) })
}

export function listContextChanges(): Promise<ContextChange[]> {
  return request('/api/organization/changes')
}

export function listAuditLog(): Promise<AccessLogEntry[]> {
  return request('/api/organization/audit-log?limit=100')
}

// Epic 32 -- labelled datasets
function datasetPath(processId: string, datasetId?: string, path = ''): string {
  return `/api/processes/${processId}/datasets${datasetId ? `/${datasetId}` : ''}${path}`
}

export function listDatasets(processId: string): Promise<DatasetSummary[]> {
  return request(datasetPath(processId))
}

export function createDataset(processId: string, body: { name: string; description?: string; agent_artifact_id?: string | null }): Promise<DatasetSummary> {
  return request(datasetPath(processId), { method: 'POST', body: JSON.stringify(body) })
}

export function getDataset(processId: string, datasetId: string): Promise<DatasetSummary> {
  return request(datasetPath(processId, datasetId))
}

export function listDatasetRows(
  processId: string,
  datasetId: string,
  params: { version?: number; segment?: string; only?: string; include_holdout?: boolean; offset?: number; limit?: number } = {},
): Promise<DatasetRowPage> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== '') query.set(key, String(value))
  return request(datasetPath(processId, datasetId, `/rows?${query.toString()}`))
}

export function addDatasetRows(processId: string, datasetId: string, rows: DatasetRowInput[]): Promise<DatasetSummary> {
  return request(datasetPath(processId, datasetId, '/rows'), { method: 'POST', body: JSON.stringify(rows) })
}

export function updateDatasetRow(
  processId: string,
  datasetId: string,
  rowKey: string,
  body: { labels?: RowLabels; segment?: string; critical?: boolean; confirm?: boolean },
): Promise<DatasetRow> {
  return request(datasetPath(processId, datasetId, `/rows/${rowKey}`), { method: 'PATCH', body: JSON.stringify(body) })
}

export function setRowAmbiguity(
  processId: string,
  datasetId: string,
  rowKey: string,
  body: { ambiguous: boolean; note?: string | null; second_labels?: RowLabels | null },
): Promise<DatasetRow> {
  return request(datasetPath(processId, datasetId, `/rows/${rowKey}/ambiguity`), { method: 'PUT', body: JSON.stringify(body) })
}

export function removeDatasetRow(processId: string, datasetId: string, rowKey: string): Promise<DatasetSummary> {
  return request(datasetPath(processId, datasetId, `/rows/${rowKey}`), { method: 'DELETE' })
}

export function getDatasetDiff(processId: string, datasetId: string, from: number, to?: number): Promise<DatasetDiff> {
  return request(datasetPath(processId, datasetId, `/diff?from=${from}${to ? `&to=${to}` : ''}`))
}

export function splitDatasetHoldout(processId: string, datasetId: string, fraction: number, seed = 7): Promise<DatasetSummary> {
  return request(datasetPath(processId, datasetId, '/holdout'), { method: 'POST', body: JSON.stringify({ fraction, seed }) })
}

export async function previewDatasetImport(processId: string, datasetId: string, file: File): Promise<ImportPreview> {
  const form = new FormData()
  form.append('file', file)
  return uploadForm(datasetPath(processId, datasetId, '/import/preview'), 'POST', form)
}

export async function importDatasetRows(
  processId: string,
  datasetId: string,
  file: File,
  mapping: ColumnMapping[],
): Promise<{ version: number; added: number; skipped: number }> {
  const form = new FormData()
  form.append('file', file)
  form.append('mapping', JSON.stringify(mapping))
  return uploadForm(datasetPath(processId, datasetId, '/import'), 'POST', form)
}

export function promoteRunToDataset(
  processId: string,
  datasetId: string,
  body: { run_id: string; labels: RowLabels; segment?: string; critical?: boolean },
): Promise<DatasetRow> {
  return request(datasetPath(processId, datasetId, '/rows/from-run'), { method: 'POST', body: JSON.stringify(body) })
}

// Epic 33 -- oracles
export function listDecisionRules(processId: string): Promise<DecisionRule[]> {
  return request(`/api/processes/${processId}/decision-rules`)
}

export function proposeDecisionRules(processId: string): Promise<DecisionRule[]> {
  return request(`/api/processes/${processId}/decision-rules/propose`, { method: 'POST' })
}

export function updateDecisionRule(
  processId: string,
  ruleId: string,
  body: { expression?: Record<string, unknown>; outcome?: string; confirm?: boolean; is_default?: boolean },
): Promise<DecisionRule> {
  return request(`/api/processes/${processId}/decision-rules/${ruleId}`, { method: 'PATCH', body: JSON.stringify(body) })
}

export function labelWithOracle(processId: string, datasetId: string): Promise<OracleLabelResult> {
  return request(datasetPath(processId, datasetId, '/oracle/label'), { method: 'POST' })
}

export function listOracleConflicts(processId: string, datasetId: string): Promise<OracleConflict[]> {
  return request(datasetPath(processId, datasetId, '/oracle/conflicts'))
}

export function reportRulesGap(processId: string, datasetId: string, rowKeys: string[]): Promise<{ gap_finding_id: string }> {
  return request(datasetPath(processId, datasetId, '/oracle/conflicts/report-gap'), { method: 'POST', body: JSON.stringify({ row_keys: rowKeys }) })
}

export function generateLabelFirst(
  processId: string,
  datasetId: string,
  body: { total: number; mix: Record<string, number>; messy_fraction?: number },
): Promise<{ queued: boolean; job_id: string | null; result: GenerateResult | null }> {
  return request(datasetPath(processId, datasetId, '/generate'), { method: 'POST', body: JSON.stringify(body) })
}

export function listRelations(processId: string, datasetId: string): Promise<MetamorphicRelation[]> {
  return request(datasetPath(processId, datasetId, '/relations'))
}

export function createRelation(
  processId: string,
  datasetId: string,
  body: { name: string; kind: string; field: string; perturbation: Record<string, unknown>; direction?: string | null },
): Promise<MetamorphicRelation> {
  return request(datasetPath(processId, datasetId, '/relations'), { method: 'POST', body: JSON.stringify(body) })
}

export function generateVariants(processId: string, datasetId: string, relationId: string): Promise<{ variants: number }> {
  return request(datasetPath(processId, datasetId, `/relations/${relationId}/variants`), { method: 'POST' })
}

export function getReviewQueue(processId: string, datasetId: string): Promise<ReviewItem[]> {
  return request(datasetPath(processId, datasetId, '/review-queue'))
}

export function listJudgeRubrics(processId: string): Promise<JudgeRubric[]> {
  return request(`/api/processes/${processId}/judge-rubrics`)
}

export function createJudgeRubric(
  processId: string,
  body: { name: string; field: string; rubric: string; min_agreement?: number; min_samples?: number },
): Promise<JudgeRubric> {
  return request(`/api/processes/${processId}/judge-rubrics`, { method: 'POST', body: JSON.stringify(body) })
}

export function calibrateJudge(processId: string, rubricId: string, datasetId: string): Promise<JudgeRubric> {
  return request(`/api/processes/${processId}/judge-rubrics/${rubricId}/calibrate/${datasetId}`, { method: 'POST' })
}

// Epic 34 -- batches
export function estimateBatch(processId: string, scenarioId: string, body: BatchRequest): Promise<{ row_keys: string[]; estimate: BatchEstimate }> {
  return request(`/api/processes/${processId}/scenarios/${scenarioId}/batches/estimate`, { method: 'POST', body: JSON.stringify(body) })
}

export function startBatch(processId: string, scenarioId: string, body: BatchRequest): Promise<BatchView> {
  return request(`/api/processes/${processId}/scenarios/${scenarioId}/batches`, { method: 'POST', body: JSON.stringify(body) })
}

export function listBatches(processId: string, scenarioId?: string): Promise<BatchView[]> {
  return request(`/api/processes/${processId}/batches${scenarioId ? `?scenario_id=${scenarioId}` : ''}`)
}

export function controlBatch(processId: string, batchId: string, action: 'pause' | 'resume' | 'cancel'): Promise<BatchView> {
  return request(`/api/processes/${processId}/batches/${batchId}/control`, { method: 'POST', body: JSON.stringify({ action }) })
}

export function listBatchSchedules(processId: string): Promise<BatchSchedule[]> {
  return request(`/api/processes/${processId}/batch-schedules`)
}

export function createBatchSchedule(
  processId: string,
  body: { scenario_id: string; tier: BatchTier; trials: number; every_minutes?: number | null; budget_usd?: number | null },
): Promise<BatchSchedule> {
  return request(`/api/processes/${processId}/batch-schedules`, { method: 'POST', body: JSON.stringify(body) })
}

export function deleteBatchSchedule(processId: string, scheduleId: string): Promise<void> {
  return request(`/api/processes/${processId}/batch-schedules/${scheduleId}`, { method: 'DELETE' })
}

// Epic 35 -- results at volume
export function getBatchResults(processId: string, batchId: string): Promise<BatchResults> {
  return request(`/api/processes/${processId}/batches/${batchId}/results`)
}

export function getBatch(processId: string, batchId: string): Promise<BatchView> {
  return request(`/api/processes/${processId}/batches/${batchId}`)
}

export function compareBatches(processId: string, a: string, b: string): Promise<BatchComparison> {
  return request(`/api/processes/${processId}/batch-comparison?a=${a}&b=${b}`)
}

export function getBatchEvidence(processId: string, batchId: string): Promise<Record<string, unknown>> {
  return request(`/api/processes/${processId}/batches/${batchId}/evidence`)
}

export async function getReleaseCriteria(processId: string, artifactId: string): Promise<ReleaseCriteria | null> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/release-criteria`)
}

export function setReleaseCriteria(processId: string, artifactId: string, body: ReleaseCriteria): Promise<ReleaseCriteria> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/release-criteria`, { method: 'PUT', body: JSON.stringify(body) })
}

export function getReleaseGate(processId: string, artifactId: string): Promise<GateResult> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/release-gate`)
}

// ---- Epic 36: process digital twin ----------------------------------------

export function getOperatingModel(processId: string): Promise<OperatingModelView> {
  return request(`/api/processes/${processId}/process-twin/operating-model`)
}

export function saveOperatingModel(processId: string, model: OperatingModel): Promise<OperatingModelView> {
  return request(`/api/processes/${processId}/process-twin/operating-model`, { method: 'PUT', body: JSON.stringify(model) })
}

export function estimateProcessTwin(processId: string, body: ProcessTwinRequest): Promise<ProcessTwinEstimate> {
  return request(`/api/processes/${processId}/process-twin/estimate`, { method: 'POST', body: JSON.stringify(body) })
}

export function startProcessTwin(processId: string, body: ProcessTwinRequest): Promise<ProcessTwinBatch> {
  return request(`/api/processes/${processId}/process-twin/batches`, { method: 'POST', body: JSON.stringify(body) })
}

export function listProcessTwinBatches(processId: string): Promise<ProcessTwinBatch[]> {
  return request(`/api/processes/${processId}/process-twin/batches`)
}

export function controlProcessTwin(processId: string, batchId: string, action: 'pause' | 'resume' | 'cancel'): Promise<ProcessTwinBatch> {
  return request(`/api/processes/${processId}/process-twin/batches/${batchId}/control`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
}

export function getProcessTwinReport(processId: string, batchId: string): Promise<EfficiencyReport> {
  return request(`/api/processes/${processId}/process-twin/batches/${batchId}/report`)
}
