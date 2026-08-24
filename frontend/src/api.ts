import type {
  AgentTaskListResponse,
  AgentTaskResponse,
  AnalysisResponse,
  HealthResponse,
  IndexResponse,
  PatchListResponse,
  PatchResponse,
  ProjectListResponse,
  ProjectMemberListResponse,
  ProjectMemberResponse,
  ProjectResponse,
  RetentionPolicyResponse,
  SandboxStatusResponse,
  SearchResponse,
  SymbolResponse,
  ToolSpec,
  AuthResponse,
  OrganizationListResponse,
  OrganizationMemberListResponse,
  OrganizationMemberResponse,
  OrganizationResponse,
  UserResponse,
  ValidatorSpec,
} from './types'
import { getFilePath } from './files'

const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'
).replace(/\/$/, '')

const ACCESS_TOKEN_KEY = 'codexxx_access_token'
let accessToken = window.sessionStorage.getItem(ACCESS_TOKEN_KEY)

export function hasStoredAccessToken(): boolean {
  return Boolean(accessToken)
}

export function setAccessToken(token: string | null): void {
  accessToken = token
  if (token) {
    window.sessionStorage.setItem(ACCESS_TOKEN_KEY, token)
  } else {
    window.sessionStorage.removeItem(ACCESS_TOKEN_KEY)
  }
}

async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`)
  }
  return fetch(input, { ...init, headers })
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await apiFetch(`${API_BASE_URL}/api/health`, { signal })

  if (!response.ok) {
    throw new Error(`后端返回 HTTP ${response.status}`)
  }

  return (await response.json()) as HealthResponse
}

export class ApiRequestError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
  }
}

type ErrorPayload = {
  detail?: unknown
}

type StructuredErrorDetail = {
  message?: unknown
}

async function getErrorMessage(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as ErrorPayload
    if (typeof payload.detail === 'string') {
      return payload.detail
    }
    if (
      typeof payload.detail === 'object' &&
      payload.detail !== null &&
      typeof (payload.detail as StructuredErrorDetail).message === 'string'
    ) {
      return (payload.detail as StructuredErrorDetail).message as string
    }
  } catch {
    // The server may return a non-JSON error page. Use the status below.
  }

  return `后端返回 HTTP ${response.status}`
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(`${API_BASE_URL}${path}`, init)
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as T
}

export const registerAccount = (
  email: string,
  displayName: string,
  password: string,
  signal?: AbortSignal,
) => requestJson<UserResponse>('/api/auth/register', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, display_name: displayName, password }),
  signal,
})

export const loginAccount = (
  email: string,
  password: string,
  signal?: AbortSignal,
) => requestJson<AuthResponse>('/api/auth/login', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, password }),
  signal,
})

export const fetchCurrentUser = (signal?: AbortSignal) =>
  requestJson<UserResponse>('/api/auth/me', { signal })

export const logoutAccount = (signal?: AbortSignal) =>
  requestJson<{ status: 'logged_out' }>('/api/auth/logout', { method: 'POST', signal })

export const deleteAccount = (
  email: string,
  currentPassword: string,
  signal?: AbortSignal,
) => requestJson<{ status: 'deleted'; deleted_user_id: string }>('/api/auth/account', {
  method: 'DELETE',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ confirm: true, email, current_password: currentPassword }),
  signal,
})

export const fetchProjects = (signal?: AbortSignal) =>
  requestJson<ProjectListResponse>('/api/projects', { signal })

export const fetchOrganizations = (signal?: AbortSignal) =>
  requestJson<OrganizationListResponse>('/api/organizations', { signal })

export const createOrganization = (
  payload: { name: string; description: string },
  signal?: AbortSignal,
) => requestJson<OrganizationResponse>('/api/organizations', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
  signal,
})

export const updateOrganization = (
  organizationId: string,
  payload: { name?: string; description?: string },
  signal?: AbortSignal,
) => requestJson<OrganizationResponse>(`/api/organizations/${organizationId}`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
  signal,
})

export const fetchOrganizationMembers = (organizationId: string, signal?: AbortSignal) =>
  requestJson<OrganizationMemberListResponse>(`/api/organizations/${organizationId}/members`, { signal })

export const addOrganizationMember = (
  organizationId: string,
  email: string,
  role: 'admin' | 'member',
  signal?: AbortSignal,
) => requestJson<OrganizationMemberResponse>(`/api/organizations/${organizationId}/members`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, role }),
  signal,
})

export const updateOrganizationMember = (
  organizationId: string,
  userId: string,
  role: 'admin' | 'member',
  signal?: AbortSignal,
) => requestJson<OrganizationMemberResponse>(
  `/api/organizations/${organizationId}/members/${userId}`,
  {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
    signal,
  },
)

export async function removeOrganizationMember(
  organizationId: string,
  userId: string,
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiFetch(
    `${API_BASE_URL}/api/organizations/${organizationId}/members/${userId}`,
    { method: 'DELETE', signal },
  )
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
}

export const createProject = (
  payload: { name: string; description: string; run_mode: 'local' | 'service'; organization_id?: string | null },
  signal?: AbortSignal,
) => requestJson<ProjectResponse>('/api/projects', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
  signal,
})

export const updateProject = (
  projectId: string,
  payload: { name?: string; description?: string; archived?: boolean },
  signal?: AbortSignal,
) => requestJson<ProjectResponse>(`/api/projects/${projectId}`, {
  method: 'PATCH',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
  signal,
})

export const deleteProject = (
  projectId: string,
  projectName: string,
  signal?: AbortSignal,
) => requestJson<{ status: 'deleted'; deleted_project_id: string }>(
  `/api/projects/${projectId}`,
  {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm: true, project_name: projectName }),
    signal,
  },
)

export const fetchProjectMembers = (projectId: string, signal?: AbortSignal) =>
  requestJson<ProjectMemberListResponse>(`/api/projects/${projectId}/members`, { signal })

export const addProjectMember = (
  projectId: string,
  email: string,
  role: 'admin' | 'editor' | 'viewer',
  signal?: AbortSignal,
) => requestJson<ProjectMemberResponse>(`/api/projects/${projectId}/members`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email, role }),
  signal,
})

export const updateProjectMember = (
  projectId: string,
  userId: string,
  role: 'admin' | 'editor' | 'viewer',
  signal?: AbortSignal,
) => requestJson<ProjectMemberResponse>(
  `/api/projects/${projectId}/members/${userId}`,
  {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
    signal,
  },
)

export async function removeProjectMember(
  projectId: string,
  userId: string,
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiFetch(
    `${API_BASE_URL}/api/projects/${projectId}/members/${userId}`,
    { method: 'DELETE', signal },
  )
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
}

export const fetchSandboxStatus = (signal?: AbortSignal) =>
  requestJson<SandboxStatusResponse>('/api/sandbox/status', { signal })

export const fetchRetentionPolicy = (signal?: AbortSignal) =>
  requestJson<RetentionPolicyResponse>('/api/retention', { signal })

export async function analyzeCode(
  question: string,
  files: File[],
  conversation: Array<{ role: 'user' | 'assistant'; content: string }>,
  signal?: AbortSignal,
): Promise<AnalysisResponse> {
  const formData = new FormData()
  formData.set('question', question)
  formData.set('conversation', JSON.stringify(conversation))

  for (const file of files) {
    formData.append('files', file, getFilePath(file))
  }

  const response = await apiFetch(`${API_BASE_URL}/api/analyze`, {
    method: 'POST',
    body: formData,
    signal,
  })

  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }

  return (await response.json()) as AnalysisResponse
}

export async function searchCode(
  query: string,
  files: File[],
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const formData = new FormData()
  formData.set('query', query)

  for (const file of files) {
    formData.append('files', file, getFilePath(file))
  }

  const response = await apiFetch(`${API_BASE_URL}/api/search`, {
    method: 'POST',
    body: formData,
    signal,
  })

  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }

  return (await response.json()) as SearchResponse
}

function appendFiles(formData: FormData, files: File[]): void {
  for (const file of files) {
    formData.append('files', file, getFilePath(file))
  }
}

export async function fetchSymbols(
  files: File[],
  query?: string,
  signal?: AbortSignal,
): Promise<SymbolResponse> {
  const formData = new FormData()
  if (query?.trim()) {
    formData.set('query', query.trim())
  }
  appendFiles(formData, files)

  const response = await apiFetch(`${API_BASE_URL}/api/symbols`, {
    method: 'POST',
    body: formData,
    signal,
  })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as SymbolResponse
}

export async function buildProjectIndex(
  files: File[],
  signal?: AbortSignal,
): Promise<IndexResponse> {
  const formData = new FormData()
  appendFiles(formData, files)

  const response = await apiFetch(`${API_BASE_URL}/api/index`, {
    method: 'POST',
    body: formData,
    signal,
  })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as IndexResponse
}

export async function fetchAgentTools(signal?: AbortSignal): Promise<ToolSpec[]> {
  const response = await apiFetch(`${API_BASE_URL}/api/tools`, { signal })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as ToolSpec[]
}

export async function fetchAgentTasks(
  projectId?: string,
  signal?: AbortSignal,
): Promise<AgentTaskListResponse> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
  const response = await apiFetch(`${API_BASE_URL}/api/tasks${query}`, { signal })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as AgentTaskListResponse
}

export async function fetchAgentTask(
  taskId: string,
  signal?: AbortSignal,
): Promise<AgentTaskResponse> {
  const response = await apiFetch(`${API_BASE_URL}/api/tasks/${taskId}`, { signal })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as AgentTaskResponse
}

export async function createAgentTask(
  goal: string,
  files: File[],
  projectId: string,
  signal?: AbortSignal,
): Promise<AgentTaskResponse> {
  const formData = new FormData()
  formData.set('goal', goal)
  formData.set('project_id', projectId)
  appendFiles(formData, files)

  const response = await apiFetch(`${API_BASE_URL}/api/tasks`, {
    method: 'POST',
    body: formData,
    signal,
  })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as AgentTaskResponse
}

async function runTaskAction(
  taskId: string,
  action: 'confirm' | 'cancel' | 'resume',
  signal?: AbortSignal,
): Promise<AgentTaskResponse> {
  const response = await apiFetch(`${API_BASE_URL}/api/tasks/${taskId}/${action}`, {
    method: 'POST',
    signal,
  })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as AgentTaskResponse
}

export const confirmAgentTask = (taskId: string, signal?: AbortSignal) =>
  runTaskAction(taskId, 'confirm', signal)

export const cancelAgentTask = (taskId: string, signal?: AbortSignal) =>
  runTaskAction(taskId, 'cancel', signal)

export const resumeAgentTask = (taskId: string, signal?: AbortSignal) =>
  runTaskAction(taskId, 'resume', signal)

export async function fetchValidators(signal?: AbortSignal): Promise<ValidatorSpec[]> {
  const response = await apiFetch(`${API_BASE_URL}/api/validators`, { signal })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as ValidatorSpec[]
}

export async function fetchTaskPatches(
  taskId: string,
  signal?: AbortSignal,
): Promise<PatchListResponse> {
  const response = await apiFetch(`${API_BASE_URL}/api/tasks/${taskId}/patches`, { signal })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as PatchListResponse
}

export async function generateTaskPatch(
  taskId: string,
  signal?: AbortSignal,
): Promise<PatchResponse> {
  const response = await apiFetch(`${API_BASE_URL}/api/tasks/${taskId}/patches/generate`, {
    method: 'POST',
    signal,
  })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as PatchResponse
}

async function postPatchAction(
  patchId: string,
  action: 'reject' | 'apply' | 'revert' | 'validate',
  body: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<PatchResponse> {
  const response = await apiFetch(`${API_BASE_URL}/api/patches/${patchId}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as PatchResponse
}

export async function reviewPatchFile(
  patchId: string,
  file: string,
  decision: 'accepted' | 'rejected',
  signal?: AbortSignal,
): Promise<PatchResponse> {
  const response = await apiFetch(`${API_BASE_URL}/api/patches/${patchId}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file, decision }),
    signal,
  })
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return (await response.json()) as PatchResponse
}

export const rejectPatch = (patchId: string, signal?: AbortSignal) =>
  postPatchAction(patchId, 'reject', {}, signal)

export const applyPatch = (patchId: string, signal?: AbortSignal) =>
  postPatchAction(patchId, 'apply', { confirm: true }, signal)

export const revertPatch = (patchId: string, signal?: AbortSignal) =>
  postPatchAction(patchId, 'revert', { confirm: true }, signal)

export const validatePatch = (
  patchId: string,
  validators: string[],
  confirmExecution = false,
  signal?: AbortSignal,
) => postPatchAction(
  patchId,
  'validate',
  { validators, confirm_execution: confirmExecution },
  signal,
)

export async function downloadPatchFile(
  patchId: string,
  file: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const encodedPath = file.split('/').map(encodeURIComponent).join('/')
  const response = await apiFetch(
    `${API_BASE_URL}/api/patches/${patchId}/files/${encodedPath}/download`,
    { signal },
  )
  if (!response.ok) {
    throw new ApiRequestError(await getErrorMessage(response), response.status)
  }
  return response.blob()
}
