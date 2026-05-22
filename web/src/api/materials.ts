// web/src/api/materials.ts
import { ApiError } from '@/api/selector'
import { getAuthHeaders } from '@/api/authHeaders'

export interface Material {
  name: string
}

export interface CreateMaterialResult {
  name: string
  created: boolean
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let env: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } | null = null
    try {
      env = await resp.json()
    } catch {
      throw new ApiError(resp.status, 'http_error', `HTTP ${resp.status}`, null)
    }
    const err = env?.error ?? {}
    throw new ApiError(
      resp.status,
      err.code ?? 'http_error',
      err.message ?? `HTTP ${resp.status}`,
      err.details ?? null,
      err.request_id,
    )
  }
  return (await resp.json()) as T
}

export async function fetchMaterials(): Promise<Material[]> {
  const resp = await fetch('/api/v1/materials', {
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
  })
  const env = await unwrap<{ materials: Material[] }>(resp)
  return env.materials
}

export async function createMaterial(name: string): Promise<CreateMaterialResult> {
  const resp = await fetch('/api/v1/materials', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    body: JSON.stringify({ name }),
  })
  return unwrap<CreateMaterialResult>(resp)
}
