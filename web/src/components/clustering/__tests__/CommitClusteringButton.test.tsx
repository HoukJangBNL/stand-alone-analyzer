import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { CommitClusteringButton } from '@/components/clustering/CommitClusteringButton'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'
import type { ReactNode } from 'react'

beforeEach(() => {
  resetClusteringStore()
  vi.unstubAllGlobals()
})

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('CommitClusteringButton', () => {
  it('POSTs apply_thresholds with current per-cluster thresholds and live max mahalanobis', async () => {
    useClusteringStore.getState().setThreshold(0, 0.7)
    useClusteringStore.getState().setThreshold(1, 0.3)
    useClusteringStore.getState().setLiveMaxMahalanobis(4.5)
    const sseBody =
      'event: progress\ndata: {"step":"apply","pct":0.5}\n\n' +
      'event: done\ndata: {"result":{"n_pass":42,"n_total":100,"n_clusters":2}}\n\n'
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(sseBody, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    )
    vi.stubGlobal('fetch', fetchMock)

    wrap(<CommitClusteringButton projectId="local" scanId={1} />)
    fireEvent.click(screen.getByRole('button', { name: /Commit clustering/ }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/clustering/apply_thresholds')
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body.cluster_thresholds).toEqual({ 0: 0.7, 1: 0.3 })
    expect(body.max_mahalanobis).toBe(4.5)
  })
})
