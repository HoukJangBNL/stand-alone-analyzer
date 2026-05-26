import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useClusteringRefit } from '@/hooks/useClusteringRefit'
import type { ReactNode } from 'react'

beforeEach(() => {
  vi.unstubAllGlobals()
})

function wrap(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

function makeSseResponse(events: string[]): Response {
  const body = events.join('\n') + '\n'
  return new Response(body, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  })
}

describe('useClusteringRefit', () => {
  it('starts running and finishes done with the result payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        makeSseResponse([
          'event: progress',
          'data: {"type":"progress","pct":0.5,"msg":"halfway"}',
          '',
          'event: done',
          'data: {"type":"done","result":{"n_clusters":2,"n_assigned":10,"n_unassigned":1,"output_dir":"/tmp"}}',
          '',
        ])
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringRefit('local', 1), { wrapper: wrap(qc) })
    await act(async () => {
      await result.current.run({
        seed_groups: [
          { name: 'a', domain_ids: [1] },
          { name: 'b', domain_ids: [2] },
        ],
      })
    })
    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.result?.n_clusters).toBe(2)
  })

  it('surfaces error events as status=error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        makeSseResponse([
          'event: error',
          'data: {"type":"error","error":{"code":"pipeline_failed","message":"oops"}}',
          '',
        ])
      )
    )
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { result } = renderHook(() => useClusteringRefit('local', 1), { wrapper: wrap(qc) })
    await act(async () => {
      await result.current.run({
        seed_groups: [{ name: 'a', domain_ids: [1] }, { name: 'b', domain_ids: [2] }],
      })
    })
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.message).toMatch(/oops/)
  })
})
