import type {
  AgentArtifact,
  AgentPublication,
  AgentPublishStatus,
  ApiErrorBody,
  BlueprintOverlay,
  BlueprintVerdict,
  BPMNDocument,
  ChatMessageResult,
  DocumentSummary,
  GapFinding,
  GapFindingStatus,
  ProcessDetail,
  ProcessSummary,
  RegistryEntry,
  RegistrySearchResult,
  RegistryStatus,
  Role,
  TwinBaseline,
  TwinExpectedStep,
  TwinHumanCheckpointConfig,
  TwinRun,
  TwinScenario,
  TwinSummary,
  TwinSystemStub,
  User,
  VersionDetail,
  VersionDiffResult,
  VersionSummary,
} from './types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

export class ApiError extends Error {
  status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

// Any request can come back 401 mid-session once the server-side session
// expires (backend/app/api/deps.py) -- not just the initial /api/auth/me
// check. AuthContext registers itself here so a 401 from *any* endpoint
// drops the user back to the login screen, instead of every call site
// having to notice and redirect itself.
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    // Epic 9/10, US9.9: the session cookie lives on the API's origin
    // (:8000), distinct from the frontend's (:3000) -- without this, the
    // browser never sends/stores it and every request looks logged-out.
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })

  if (!response.ok) {
    const detail = await readErrorDetail(response)
    if (response.status === 401) unauthorizedListener?.()
    throw new ApiError(response.status, detail)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}

export function login(name: string, role: Role = 'viewer'): Promise<User> {
  return request('/api/auth/login', { method: 'POST', body: JSON.stringify({ name, role }) })
}

export function logout(): Promise<void> {
  return request('/api/auth/logout', { method: 'POST' })
}

export async function getCurrentUser(): Promise<User | null> {
  try {
    return await request<User>('/api/auth/me')
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

// Not JSON (returns text/markdown), so this bypasses the request() helper.
export async function exportBlueprint(processId: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/processes/${processId}/blueprint/export?format=markdown`, {
    credentials: 'include',
  })
  if (!response.ok) {
    const detail = await readErrorDetail(response)
    if (response.status === 401) unauthorizedListener?.()
    throw new ApiError(response.status, detail)
  }
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

  const response = await fetch(`${API_BASE_URL}/api/processes/${processId}/documents`, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  })

  if (!response.ok) {
    const detail = await readErrorDetail(response)
    if (response.status === 401) unauthorizedListener?.()
    throw new ApiError(response.status, detail)
  }

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

export function createScenario(
  processId: string,
  artifactId: string,
  body: {
    name: string
    inputs?: Record<string, unknown>
    system_stubs?: Record<string, TwinSystemStub>
    human_checkpoint_config?: TwinHumanCheckpointConfig
    expected_steps?: TwinExpectedStep[]
    expected_outputs?: Record<string, unknown>
  },
): Promise<TwinScenario> {
  return request(`/api/processes/${processId}/agent-artifacts/${artifactId}/scenarios`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function deleteScenario(processId: string, scenarioId: string): Promise<void> {
  return request(`/api/processes/${processId}/scenarios/${scenarioId}`, { method: 'DELETE' })
}

export function runScenario(processId: string, scenarioId: string): Promise<TwinRun> {
  return request(`/api/processes/${processId}/scenarios/${scenarioId}/run`, { method: 'POST' })
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
