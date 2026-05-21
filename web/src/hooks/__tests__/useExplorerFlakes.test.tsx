import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useExplorerFlakes } from '@/hooks/useExplorerFlakes'
import { useExplorerFlakeDetail } from '@/hooks/useExplorerFlakeDetail'

beforeEach(() => { vi.unstubAllGlobals() })

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('useExplorerFlakes', () => {
  it('passes include/exclude/size to the URL and returns rows', async () => {
    const captured: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      captured.push(url)
      return new Response(JSON.stringify({
        rows: [{ flake_id: 1, image_id: 0, domains: 3,
                 groups: 'thin', distance: '—', clipped: 'no', pass: true }],
        total: 1,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    const { result } = renderHook(
      () => useExplorerFlakes('local', {
        include: ['thin'], exclude: [], sizeMin: 1, sizeMax: 50,
      }),
      { wrapper: wrap() },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.total).toBe(1)
    expect(captured[0]).toContain('include=thin')
    expect(captured[0]).toContain('size_min=1')
    expect(captured[0]).toContain('size_max=50')
  })

  it('refetches when filter args change (different queryKey)', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      calls.push(url)
      return new Response(JSON.stringify({ rows: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    }))
    const { result, rerender } = renderHook(
      ({ q }: { q: { include: string[]; exclude: string[];
                     sizeMin: number | null; sizeMax: number | null } }) =>
        useExplorerFlakes('local', q),
      {
        wrapper: wrap(),
        initialProps: { q: { include: ['thin'], exclude: [], sizeMin: null, sizeMax: null } },
      },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    rerender({ q: { include: ['thick'], exclude: [], sizeMin: null, sizeMax: null } })
    await waitFor(() => expect(calls.length).toBeGreaterThanOrEqual(2))
    expect(calls[calls.length - 1]).toContain('include=thick')
  })
})

describe('useExplorerFlakeDetail', () => {
  it('is disabled when flakeId is null', async () => {
    const f = vi.fn(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', f)
    const { result } = renderHook(
      () => useExplorerFlakeDetail('local', null),
      { wrapper: wrap() },
    )
    // disabled query: never fires
    await new Promise((r) => setTimeout(r, 30))
    expect(f).not.toHaveBeenCalled()
    expect(result.current.fetchStatus).toBe('idle')
  })

  it('fetches when flakeId is non-null', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        flake_id: 42, image_id: 7, domain_ids: [10], cluster_names: ['thin'],
        bbox_xy: [], mask_stats: {}, distance_px: null, isolation_px: null,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { result } = renderHook(
      () => useExplorerFlakeDetail('local', 42),
      { wrapper: wrap() },
    )
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.flake_id).toBe(42)
  })
})
