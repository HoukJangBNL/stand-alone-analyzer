// web/src/api/upload.ts
import { ApiError } from '@/api/selector'
import { getAuthHeaders } from '@/api/authHeaders'

export interface CreateScanBody {
  name: string
  material: string
  image_count: number
  extra_metadata: Record<string, string>
}
export interface CreateScanResult {
  scan_id: string
}

export interface PresignBody {
  filename: string
  sha256_hex: string
  size_bytes: number
  grid_ix: number
  grid_iy: number
}
export interface PresignResult {
  put_url: string
  headers: Record<string, string>
  upload_item_id: string
}

export interface CompleteResult {
  image_id: string
}

export interface FinalizeResult {
  status: 'ready' | string
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

export async function createScan(
  projectId: string,
  body: CreateScanBody,
  signal?: AbortSignal,
): Promise<CreateScanResult> {
  const resp = await fetch(`/api/v1/projects/${projectId}/scans`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    body: JSON.stringify(body),
    signal,
  })
  return unwrap<CreateScanResult>(resp)
}

export async function presignImage(
  scanId: string,
  body: PresignBody,
  signal?: AbortSignal,
): Promise<PresignResult> {
  const resp = await fetch(`/api/v1/scans/${scanId}/images/presign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    body: JSON.stringify(body),
    signal,
  })
  return unwrap<PresignResult>(resp)
}

export async function completeImage(
  scanId: string,
  uploadItemId: string,
  signal?: AbortSignal,
): Promise<CompleteResult> {
  const resp = await fetch(`/api/v1/scans/${scanId}/images/${uploadItemId}/complete`, {
    method: 'POST',
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    signal,
  })
  return unwrap<CompleteResult>(resp)
}

export async function finalizeScan(scanId: string, signal?: AbortSignal): Promise<FinalizeResult> {
  const resp = await fetch(`/api/v1/scans/${scanId}/finalize`, {
    method: 'POST',
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    signal,
  })
  return unwrap<FinalizeResult>(resp)
}

export async function putToS3(
  putUrl: string,
  file: File,
  headers: Record<string, string>,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch(putUrl, {
    method: 'PUT',
    headers,
    body: file,
    signal,
  })
  if (!resp.ok) {
    throw new Error(`S3 PUT failed: ${resp.status} ${resp.statusText}`)
  }
}

export interface ScanSummary {
  scan_id: number
  name: string
  material: string
  image_count: number
  created_at: string
}

interface ScanListEnvelope {
  scans: ScanSummary[]
}

export async function listScansForProject(projectId: string): Promise<ScanSummary[]> {
  const resp = await fetch(`/api/v1/projects/${projectId}/scans`, {
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
  })
  const env = await unwrap<ScanListEnvelope>(resp)
  return env.scans
}
