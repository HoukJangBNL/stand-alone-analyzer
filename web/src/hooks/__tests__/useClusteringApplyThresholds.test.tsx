import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useClusteringApplyThresholds } from '@/hooks/useClusteringApplyThresholds'
import type { ReactNode } from 'react'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function wrap(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function sse(events: string[]): Response {
  return new Response(events.join('\n') + '\n', {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
}

describe('useClusteringApplyThresholds', () => {
  it('marks done with the apply summary', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        sse([
          'event: done',
          'data: {"type":"done","result":{"n_pass":80,"n_total":150,"n_clusters":3}}',
          '',
        ])
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringApplyThresholds('local'), { wrapper: wrap(qc) })
    await act(async () => {
      await result.current.run({ cluster_thresholds: { 0: 0.5 } })
    })
    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.result?.n_pass).toBe(80)
    expect(result.current.result?.n_total).toBe(150)
  })
})
