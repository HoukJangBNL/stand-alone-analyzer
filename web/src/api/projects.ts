// web/src/api/projects.ts
import { ApiError } from '@/api/selector'

export interface ProjectHandle {
  project_id: string
  analysis_folder: string
  raw_images_dir?: string | null
  annotations_path?: string | null
}

export interface CreateProjectBody {
  analysis_folder?: string | null
  raw_images_dir?: string | null
  annotations_path?: string | null
}

interface ProjectListEnvelope {
  projects: ProjectHandle[]
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let envelope: {
      error?: {
        code?: string
        message?: string
        details?: unknown
        request_id?: string
      }
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

/**
 * GET /api/v1/projects — list all projects.
 *
 * NOTE (W3.2 mock contract): backend currently has no list endpoint. We hit the
 * URL anyway and on HTTP 404 we fall back to GET /projects/active, returning a
 * single-element list. When backend W2.1 ships the real list, no caller change.
 */
export async function fetchProjects(): Promise<ProjectHandle[]> {
  const resp = await fetch('/api/v1/projects', { headers: { Accept: 'application/json' } })
  // Backend currently exposes only POST /projects (no list yet). FastAPI replies
  // 405 for GET on a path with another method, 404 if the path is absent — both
  // mean "no list endpoint", fall back to the single active project.
  if (resp.status === 404 || resp.status === 405) {
    return [await fetchActiveProject()]
  }
  const env = await unwrap<ProjectListEnvelope>(resp)
  return env.projects
}

export async function fetchActiveProject(): Promise<ProjectHandle> {
  const resp = await fetch('/api/v1/projects/active', {
    headers: { Accept: 'application/json' },
  })
  return unwrap<ProjectHandle>(resp)
}

export async function createProject(body: CreateProjectBody): Promise<ProjectHandle> {
  const resp = await fetch('/api/v1/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  return unwrap<ProjectHandle>(resp)
}
