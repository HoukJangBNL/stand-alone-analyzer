// web/src/api/__tests__/selector.test.ts
import { describe, expect, it, vi, beforeEach } from 'vitest'
import {
  fetchDomainStats,
  fetchSelection,
  postCommit,
  buildPreviewUrl,
  buildExportUrl,
} from '@/api/selector'

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('fetchDomainStats', () => {
  it('GETs /api/v1/projects/{pid}/data/domain_stats and parses JSON', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          flake_ids: [1, 2],
          mean_r: [10, 20], mean_g: [30, 40], mean_b: [50, 60],
          std_r: [1, 2], std_g: [3, 4], std_b: [5, 6],
          areas: [100, 200],
          sam2: [0.1, 0.5],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)

    const out = await fetchDomainStats('local')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects/local/data/domain_stats',
      expect.any(Object)
    )
    expect(out.flake_ids).toEqual([1, 2])
    expect(out.sam2).toEqual([0.1, 0.5])
  })

  it('throws ApiError on non-2xx', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: 'domain_stats_not_found', message: 'no npz' } }),
          { status: 404, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    await expect(fetchDomainStats('local')).rejects.toThrow(/domain_stats_not_found/)
  })
})

describe('fetchSelection', () => {
  it('parses {domain_id, selected} columns', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ domain_id: [1, 2, 3], selected: [true, false, true] }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    const out = await fetchSelection('local')
    expect(out.domain_id).toEqual([1, 2, 3])
    expect(out.selected).toEqual([true, false, true])
  })
})

describe('postCommit', () => {
  it('POSTs JSON body with params + lasso_ids', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          output_path: '/p/03_selector/selection.parquet',
          n_committed: 1,
          n_filter_accepted: 3,
          n_lasso: 2,
          total_count: 4,
          params_hash: 'sha256:zzz',
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)

    const out = await postCommit('local', { params: { area_min: 5 } as any, lasso_ids: [2, 3] })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects/local/selector/commit',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'Content-Type': 'application/json' }),
      })
    )
    expect(out.n_committed).toBe(1)
  })
})

describe('buildPreviewUrl + buildExportUrl', () => {
  it('builds preview url with optional contour', () => {
    expect(buildPreviewUrl('local', 7, false))
      .toBe('/api/v1/projects/local/data/annotations/7/preview?with_contour=false')
    expect(buildPreviewUrl('local', 7, true))
      .toBe('/api/v1/projects/local/data/annotations/7/preview?with_contour=true')
  })
  it('builds export url', () => {
    expect(buildExportUrl('local', 'selected'))
      .toBe('/api/v1/projects/local/selector/export?mode=selected')
    expect(buildExportUrl('local', 'filtered'))
      .toBe('/api/v1/projects/local/selector/export?mode=filtered')
  })
})
