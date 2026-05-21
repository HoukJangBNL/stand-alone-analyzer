import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { useSaveExplorerState } from '@/hooks/useSaveExplorerState'

beforeEach(() => { vi.unstubAllGlobals() })

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    qc,
    Wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    ),
  }
}

describe('useSaveExplorerState', () => {
  it('runs the POST and resolves with the result envelope', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        state_path: '/tmp/explorer_state.json', selected_count: 2,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { Wrapper } = wrap()
    const { result } = renderHook(
      () => useSaveExplorerState('local'),
      { wrapper: Wrapper },
    )
    await act(async () => {
      await result.current.mutateAsync({
        include_labels: ['thin'], exclude_labels: [],
        neighbor_filter: { size_min: null, size_max: null,
                           isolation_min: null, exclude_border_clipped: false },
        selected_flake_ids: [1, 2],
      })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.selected_count).toBe(2)
  })

  it('exposes ApiError on 409', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'prerequisite_missing', message: 'fit clustering first',
                 details: {}, request_id: 'r' },
      }), { status: 409, headers: { 'content-type': 'application/json' } })
    ))
    const { Wrapper } = wrap()
    const { result } = renderHook(
      () => useSaveExplorerState('local'),
      { wrapper: Wrapper },
    )
    await act(async () => {
      try {
        await result.current.mutateAsync({
          include_labels: [], exclude_labels: [],
          neighbor_filter: { size_min: null, size_max: null,
                             isolation_min: null, exclude_border_clipped: false },
        })
      } catch { /* expected */ }
    })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as any)?.code).toBe('prerequisite_missing')
  })

  it('invalidates the explorer state query on success', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        state_path: '/tmp/explorer_state.json', selected_count: null,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    const { qc, Wrapper } = wrap()
    const spy = vi.spyOn(qc, 'invalidateQueries')
    const { result } = renderHook(
      () => useSaveExplorerState('local'),
      { wrapper: Wrapper },
    )
    await act(async () => {
      await result.current.mutateAsync({
        include_labels: [], exclude_labels: [],
        neighbor_filter: { size_min: null, size_max: null,
                           isolation_min: null, exclude_border_clipped: false },
      })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(spy).toHaveBeenCalledWith({ queryKey: ['explorer', 'state', 'local'] })
  })
})
