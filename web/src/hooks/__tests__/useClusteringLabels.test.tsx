import { describe, expect, it, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useClusteringLabels } from '@/hooks/useClusteringLabels'
import type { ReactNode } from 'react'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function wrap(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('useClusteringLabels', () => {
  it('returns labels.json on success', async () => {
    const payload = {
      version: 1, n_clusters: 1,
      groups: [{ id: 0, name: 'a', size: 5, mean_rgb: [0.1, 0.2, 0.3] }],
      assignments: { '1': 0 }, thresholds: { '0': 0.5 },
      noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringLabels('local'), { wrapper: wrap(qc) })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.n_clusters).toBe(1)
  })

  it('surfaces a 404 ApiError when clustering not fitted', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }),
          { status: 404, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringLabels('local'), { wrapper: wrap(qc) })
    await waitFor(() => expect(result.current.isError).toBe(true))
    expect((result.current.error as any).code).toBe('clustering_not_fitted')
  })
})
