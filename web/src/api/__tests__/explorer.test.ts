import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  fetchTileManifest,
  fetchExplorerGrid,
  fetchExplorerFlakes,
  fetchExplorerFlakeDetail,
  saveExplorerState,
  getExplorerState,
} from '@/api/explorer'
import { ApiError } from '@/api/selector'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function makeFetch(url_to_resp: Record<string, () => Response>) {
  return vi.fn(async (url: string) => {
    for (const [k, fn] of Object.entries(url_to_resp)) {
      if (url.includes(k)) return fn()
    }
    throw new Error(`unmocked URL: ${url}`)
  })
}

describe('api/explorer — fetchTileManifest', () => {
  it('returns the parsed JSON payload on 200', async () => {
    const tm = {
      grid_w: 2, grid_h: 1,
      lod_sizes: { '0': [64, 48] }, signature: ['s0', 's1'],
      params_hash: 'h', tiles: [],
    }
    vi.stubGlobal('fetch', makeFetch({
      '/explorer/tile_manifest': () =>
        new Response(JSON.stringify(tm), { status: 200,
          headers: { 'content-type': 'application/json' } }),
    }))
    const out = await fetchTileManifest('local')
    expect(out.grid_w).toBe(2)
    expect(out.params_hash).toBe('h')
  })

  it('throws ApiError on 404 with the envelope code', async () => {
    vi.stubGlobal('fetch', makeFetch({
      '/explorer/tile_manifest': () =>
        new Response(JSON.stringify({
          error: { code: 'artifact_missing', message: 'no thumbs', details: {}, request_id: 'r' },
        }), { status: 404, headers: { 'content-type': 'application/json' } }),
    }))
    await expect(fetchTileManifest('local')).rejects.toBeInstanceOf(ApiError)
  })
})

describe('api/explorer — fetchExplorerGrid', () => {
  it('hits /explorer/grid', async () => {
    const f = makeFetch({
      '/explorer/grid': () =>
        new Response(JSON.stringify({
          grid_w: 1, grid_h: 1, lod_sizes: {}, signature: [], params_hash: 'g', tiles: [],
        }), { status: 200, headers: { 'content-type': 'application/json' } }),
    })
    vi.stubGlobal('fetch', f)
    const out = await fetchExplorerGrid('local')
    expect(out.params_hash).toBe('g')
    expect(f).toHaveBeenCalledWith(
      expect.stringContaining('/explorer/grid'),
      expect.any(Object),
    )
  })
})

describe('api/explorer — fetchExplorerFlakes', () => {
  it('encodes include/exclude/size_min/size_max as query params', async () => {
    const captured: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      captured.push(url)
      return new Response(JSON.stringify({ rows: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    await fetchExplorerFlakes('local', {
      include: ['thin', 'thick'], exclude: ['noise'],
      sizeMin: 1, sizeMax: 50,
    })
    expect(captured[0]).toContain('include=thin%2Cthick')
    expect(captured[0]).toContain('exclude=noise')
    expect(captured[0]).toContain('size_min=1')
    expect(captured[0]).toContain('size_max=50')
  })

  it('omits empty filters from the query', async () => {
    const captured: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      captured.push(url)
      return new Response(JSON.stringify({ rows: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    await fetchExplorerFlakes('local', {
      include: [], exclude: [], sizeMin: null, sizeMax: null,
    })
    expect(captured[0]).not.toContain('include=')
    expect(captured[0]).not.toContain('exclude=')
    expect(captured[0]).not.toContain('size_min=')
  })
})

describe('api/explorer — fetchExplorerFlakeDetail', () => {
  it('hits /explorer/flake/{id}', async () => {
    vi.stubGlobal('fetch', makeFetch({
      '/explorer/flake/42': () =>
        new Response(JSON.stringify({
          flake_id: 42, image_id: 7,
          domain_ids: [10, 11], cluster_names: ['thin'],
          bbox_xy: [], mask_stats: {},
          distance_px: null, isolation_px: null,
        }), { status: 200, headers: { 'content-type': 'application/json' } }),
    }))
    const out = await fetchExplorerFlakeDetail('local', 42)
    expect(out.flake_id).toBe(42)
    expect(out.cluster_names).toEqual(['thin'])
  })
})

describe('api/explorer — saveExplorerState', () => {
  it('POSTs JSON and returns the result envelope', async () => {
    const f = vi.fn(async (_url: string, init: RequestInit) => {
      expect(init.method).toBe('POST')
      return new Response(JSON.stringify({
        state_path: '/tmp/explorer_state.json', selected_count: 3,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    })
    vi.stubGlobal('fetch', f)
    const out = await saveExplorerState('local', {
      include_labels: ['thin'], exclude_labels: [],
      neighbor_filter: { size_min: 1, size_max: 50,
                         isolation_min: null, exclude_border_clipped: false },
      selected_flake_ids: [1, 2, 3],
    })
    expect(out.selected_count).toBe(3)
  })

  it('throws ApiError on 409', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'prerequisite_missing', message: 'commit clustering first',
                 details: {}, request_id: 'r' },
      }), { status: 409, headers: { 'content-type': 'application/json' } })
    ))
    await expect(saveExplorerState('local', {
      include_labels: [], exclude_labels: [],
      neighbor_filter: { size_min: null, size_max: null,
                         isolation_min: null, exclude_border_clipped: false },
    })).rejects.toBeInstanceOf(ApiError)
  })
})

describe('api/explorer — getExplorerState', () => {
  it('returns the saved JSON on 200', async () => {
    vi.stubGlobal('fetch', makeFetch({
      '/run/explorer/state': () =>
        new Response(JSON.stringify({
          include_labels: ['thin'], exclude_labels: [],
          neighbor_filter: {}, saved_at: '2026-05-21T00:00:00Z',
        }), { status: 200, headers: { 'content-type': 'application/json' } }),
    }))
    const out = await getExplorerState('local')
    expect(out.include_labels).toEqual(['thin'])
  })

  it('throws ApiError with code explorer_state_missing on 404', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'explorer_state_missing', message: '404',
                 details: {}, request_id: 'r' },
      }), { status: 404, headers: { 'content-type': 'application/json' } })
    ))
    await expect(getExplorerState('local')).rejects.toBeInstanceOf(ApiError)
  })
})
