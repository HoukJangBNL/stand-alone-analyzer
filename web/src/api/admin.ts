// web/src/api/admin.ts
import { ApiError } from '@/api/selector'
import type { UserRole } from '@/api/auth'

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let envelope: any = null
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

export interface UsageEvent {
  id: string
  user_id: string
  kind: string
  value_json?: Record<string, unknown>
  ts: string
}

export interface UsageQueryParams {
  user_id?: string
  kind?: string
  since?: string
  until?: string
  limit?: number
  aggregate?: boolean
}

export async function fetchUsage(params?: UsageQueryParams): Promise<UsageEvent[]> {
  const query = new URLSearchParams()
  if (params?.user_id) query.set('user_id', params.user_id)
  if (params?.kind) query.set('kind', params.kind)
  if (params?.since) query.set('since', params.since)
  if (params?.until) query.set('until', params.until)
  if (params?.limit) query.set('limit', params.limit.toString())
  if (params?.aggregate) query.set('aggregate', 'true')

  const resp = await fetch(`/api/v1/admin/usage?${query}`, {
    headers: { Accept: 'application/json' },
    credentials: 'include',
  })
  return unwrap<UsageEvent[]>(resp)
}

export async function updateUserRole(userId: string, role: UserRole): Promise<void> {
  const resp = await fetch(`/api/v1/admin/users/${userId}/role`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ role }),
  })
  if (!resp.ok) {
    throw new ApiError(resp.status, 'update_role_failed', `HTTP ${resp.status}`, null)
  }
}

export async function deactivateUser(userId: string): Promise<void> {
  const resp = await fetch(`/api/v1/admin/users/${userId}/deactivate`, {
    method: 'POST',
    credentials: 'include',
  })
  if (!resp.ok) {
    throw new ApiError(resp.status, 'deactivate_failed', `HTTP ${resp.status}`, null)
  }
}

export async function reactivateUser(userId: string): Promise<void> {
  const resp = await fetch(`/api/v1/admin/users/${userId}/reactivate`, {
    method: 'POST',
    credentials: 'include',
  })
  if (!resp.ok) {
    throw new ApiError(resp.status, 'reactivate_failed', `HTTP ${resp.status}`, null)
  }
}

export type ProjectRole = 'viewer' | 'editor'

export async function updateProjectAcl(
  projectId: string,
  userId: string,
  projectRole: ProjectRole
): Promise<void> {
  const resp = await fetch(`/api/v1/admin/projects/${projectId}/acl`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ user_id: userId, project_role: projectRole }),
  })
  if (!resp.ok) {
    throw new ApiError(resp.status, 'update_acl_failed', `HTTP ${resp.status}`, null)
  }
}

export async function deleteProjectAcl(projectId: string, userId: string): Promise<void> {
  const resp = await fetch(`/api/v1/admin/projects/${projectId}/acl/${userId}`, {
    method: 'DELETE',
    credentials: 'include',
  })
  if (!resp.ok) {
    throw new ApiError(resp.status, 'delete_acl_failed', `HTTP ${resp.status}`, null)
  }
}
