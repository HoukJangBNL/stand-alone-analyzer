import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useTileManifest } from '@/hooks/useTileManifest'
import { useExplorerGrid } from '@/hooks/useExplorerGrid'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function wrap() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('useTileManifest', () => {
  it('returns the parsed TileManifest on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        grid_w: 4, grid_h: 3,
        lod_sizes: { '0': [64, 48] }, signature: ['s0', 's1'],
        params_hash: 'h', tiles: [],
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { result } = renderHook(() => useTileManifest('local'), { wrapper: wrap() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.grid_w).toBe(4)
    expect(result.current.data?.params_hash).toBe('h')
  })

  it('exposes the ApiError code on failure', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'artifact_missing', message: '404',
                 details: {}, request_id: 'r' },
      }), { status: 404, headers: { 'content-type': 'application/json' } })
    ))
    const { result } = renderHook(() => useTileManifest('local'), { wrapper: wrap() })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as any)?.code).toBe('artifact_missing')
  })
})

describe('useExplorerGrid', () => {
  it('returns the parsed grid payload', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        grid_w: 1, grid_h: 1, lod_sizes: {}, signature: [],
        params_hash: 'g', tiles: [],
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { result } = renderHook(() => useExplorerGrid('local'), { wrapper: wrap() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.params_hash).toBe('g')
  })
})
