// web/src/api/explorer.ts
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

export interface TileManifestEntryDto {
  image_id: number
  stem: string
  col: number
  row: number
  width_px: number
  height_px: number
  lod_sizes: Record<string, [number, number]>
}

export interface TileManifestDto {
  grid_w: number
  grid_h: number
  lod_sizes: Record<string, [number, number]>
  signature: string[]
  params_hash: string
  tiles: TileManifestEntryDto[]
}

export interface ExplorerFlakeRowDto {
  flake_id: number
  image_id: number
  domains: number
  groups: string
  distance: string
  clipped: string
  pass: boolean
}

export interface ExplorerFlakesResponseDto {
  rows: ExplorerFlakeRowDto[]
  total: number
}

export interface ExplorerFlakeDetailDto {
  flake_id: number
  image_id: number
  domain_ids: number[]
  cluster_names: string[]
  bbox_xy: number[]
  mask_stats: Record<string, number>
  distance_px: number | null
  isolation_px: number | null
}

export interface SaveExplorerStateBody {
  include_labels: string[]
  exclude_labels: string[]
  neighbor_filter: {
    size_min: number | null
    size_max: number | null
    isolation_min: number | null
    exclude_border_clipped: boolean
  }
  selected_flake_ids?: number[]
}

export interface SaveExplorerStateResultDto {
  state_path: string
  selected_count: number | null
}

export async function fetchTileManifest(projectId: string): Promise<TileManifestDto> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/explorer/tile_manifest`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<TileManifestDto>(resp)
}

export async function fetchExplorerGrid(projectId: string): Promise<TileManifestDto> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/explorer/grid`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<TileManifestDto>(resp)
}

export interface ExplorerFlakesQuery {
  include: string[]
  exclude: string[]
  sizeMin: number | null
  sizeMax: number | null
}

export async function fetchExplorerFlakes(
  projectId: string,
  q: ExplorerFlakesQuery
): Promise<ExplorerFlakesResponseDto> {
  const params = new URLSearchParams()
  if (q.include.length > 0) params.set('include', q.include.join(','))
  if (q.exclude.length > 0) params.set('exclude', q.exclude.join(','))
  if (q.sizeMin !== null) params.set('size_min', String(q.sizeMin))
  if (q.sizeMax !== null) params.set('size_max', String(q.sizeMax))
  const qs = params.toString()
  const url = `/api/v1/projects/${projectId}/explorer/flakes${qs ? `?${qs}` : ''}`
  const resp = await fetch(url, { headers: { Accept: 'application/json' } })
  return unwrap<ExplorerFlakesResponseDto>(resp)
}

export async function fetchExplorerFlakeDetail(
  projectId: string,
  flakeId: number
): Promise<ExplorerFlakeDetailDto> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/explorer/flake/${flakeId}`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<ExplorerFlakeDetailDto>(resp)
}

export async function saveExplorerState(
  projectId: string,
  body: SaveExplorerStateBody
): Promise<SaveExplorerStateResultDto> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/run/explorer/save_state`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    }
  )
  return unwrap<SaveExplorerStateResultDto>(resp)
}

export async function getExplorerState(projectId: string): Promise<any> {
  const resp = await fetch(
    `/api/v1/projects/${projectId}/run/explorer/state`,
    { headers: { Accept: 'application/json' } }
  )
  return unwrap<any>(resp)
}
