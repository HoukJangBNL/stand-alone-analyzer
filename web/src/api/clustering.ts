// web/src/api/clustering.ts
import { ApiError } from '@/api/selector'

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

export interface SeedGroupDto {
  name: string
  domain_ids: number[]
}

export interface LabelsGroup {
  id: number
  name: string
  size: number
  mean_rgb: [number, number, number]
}

export interface LabelsJson {
  version: number
  n_clusters: number
  groups: LabelsGroup[]
  assignments: Record<string, number>
  thresholds: Record<string, number>
  noise_label: number
  random_state: number
  fitted_at: string
  max_mahalanobis?: number
}

export interface AssignmentsRows {
  domain_id: number[]
  cluster_label: number[]
  max_posterior: number[]
  nearest_mahalanobis?: number[]
  threshold_pass?: boolean[]
}

export interface ClusteringRefitBody {
  seed_groups: SeedGroupDto[]
  feature_cols?: string[]
  covariance_type?: 'full' | 'tied' | 'diag' | 'spherical'
  rgb_threshold?: number
  fit_scope?: 'seeds' | 'all_selected'
  max_mahalanobis?: number
}

export interface ApplyThresholdsBody {
  cluster_thresholds: Record<number, number>
  max_mahalanobis?: number | null
}

export async function fetchClusteringLabels(projectId: string): Promise<LabelsJson> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/data/clustering/labels`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<LabelsJson>(resp)
}

export async function fetchClusteringAssignments(
  projectId: string
): Promise<AssignmentsRows> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/data/clustering/assignments`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<AssignmentsRows>(resp)
}

export async function fetchClusteringSeedGroups(
  projectId: string
): Promise<SeedGroupDto[]> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/data/clustering/seed_groups`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<SeedGroupDto[]>(resp)
}
