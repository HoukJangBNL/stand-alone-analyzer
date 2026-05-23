// web/src/api/projects.ts
import { ApiError } from '@/api/selector'
import { getAuthHeaders } from '@/api/authHeaders'

export interface Project {
  project_id: string
  name: string
  description: string | null
  created_at: string
  scan_count: number
}

export interface CreateProjectBody {
  name: string
  description?: string
}

export interface PatchProjectBody {
  name?: string
  description?: string | null
}

interface ProjectListEnvelope {
  projects: Project[]
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (resp.status === 204) return undefined as unknown as T
  if (!resp.ok) {
    let envelope: {
      error?: { code?: string; message?: string; details?: unknown; request_id?: string }
    } | null = null
    try {
      envelope = await resp.json()
    } catch {
      throw new ApiError(resp.status, 'http_error', `HTTP ${resp.status}`, null)
    }
    const err = envelope?.error ?? {}
    throw new ApiError(
      resp.status,
      err.code ?? 'http_error',
      err.message ?? `HTTP ${resp.status}`,
      err.details ?? null,
      err.request_id
    )
  }
  return (await resp.json()) as T
}

export async function listProjects(): Promise<Project[]> {
  const resp = await fetch('/api/v1/projects', {
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
  })
  const env = await unwrap<ProjectListEnvelope>(resp)
  return env.projects
}

export async function createProject(body: CreateProjectBody): Promise<Project> {
  // Strip undefined description so the body is `{name: ...}` only when omitted.
  const payload: Record<string, string> = { name: body.name }
  if (body.description !== undefined) payload.description = body.description
  const resp = await fetch('/api/v1/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    body: JSON.stringify(payload),
  })
  return unwrap<Project>(resp)
}

export async function getProject(projectId: string): Promise<Project> {
  const resp = await fetch(`/api/v1/projects/${projectId}`, {
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
  })
  return unwrap<Project>(resp)
}

export async function patchProject(projectId: string, body: PatchProjectBody): Promise<Project> {
  const resp = await fetch(`/api/v1/projects/${projectId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    body: JSON.stringify(body),
  })
  return unwrap<Project>(resp)
}

export async function deleteProject(projectId: string): Promise<void> {
  const resp = await fetch(`/api/v1/projects/${projectId}`, {
    method: 'DELETE',
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
  })
  await unwrap<void>(resp)
}
