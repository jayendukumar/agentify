import type {
  ApiErrorBody,
  BlueprintOverlay,
  BPMNDocument,
  ChatMessageResult,
  DocumentSummary,
  ProcessDetail,
  ProcessSummary,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as ApiErrorBody
      detail = body.detail ?? detail
    } catch {
      // response body wasn't JSON -- fall back to statusText
    }
    throw new ApiError(response.status, detail)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
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

export async function uploadDocuments(processId: string, files: FileList | File[]): Promise<DocumentSummary[]> {
  const formData = new FormData()
  for (const file of Array.from(files)) {
    formData.append('files', file)
  }

  const response = await fetch(`${API_BASE_URL}/api/processes/${processId}/documents`, {
    method: 'POST',
    body: formData,
  })

  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as ApiErrorBody
      detail = body.detail ?? detail
    } catch {
      // response body wasn't JSON -- fall back to statusText
    }
    throw new ApiError(response.status, detail)
  }

  return (await response.json()) as DocumentSummary[]
}
