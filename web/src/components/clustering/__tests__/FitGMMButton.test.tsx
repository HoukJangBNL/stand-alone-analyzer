import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { FitGMMButton } from '@/components/clustering/FitGMMButton'
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

describe('FitGMMButton', () => {
  it('is disabled when seedGroups.length < 2', () => {
    wrap(<FitGMMButton projectId="local" scanId={1} />)
    const btn = screen.getByRole('button', { name: /Fit GMM/ })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
    useClusteringStore.getState().addSeedGroup('a', [1])
    wrap(<FitGMMButton projectId="local" scanId={1} />)
    const stillDisabled = screen.getAllByRole('button', { name: /Fit GMM/ }).at(-1) as HTMLButtonElement
    expect(stillDisabled.disabled).toBe(true)
  })

  it('is enabled with 2+ seed groups and POSTs refit on click', async () => {
    useClusteringStore.getState().addSeedGroup('a', [1])
    useClusteringStore.getState().addSeedGroup('b', [2])
    const sseBody =
      'event: progress\ndata: {"step":"refit","pct":0.5}\n\n' +
      'event: done\ndata: {"result":{"n_clusters":2}}\n\n'
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(sseBody, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    )
    vi.stubGlobal('fetch', fetchMock)

    wrap(<FitGMMButton projectId="local" scanId={1} />)
    const btn = screen.getByRole('button', { name: /Fit GMM/ }) as HTMLButtonElement
    expect(btn.disabled).toBe(false)
    fireEvent.click(btn)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/clustering/refit')
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body.seed_groups).toEqual([
      { name: 'a', domain_ids: [1] },
      { name: 'b', domain_ids: [2] },
    ])
    expect(body.fit_scope).toBe('seeds')
  })

  it('submits regCovar from the slice (default 10.0) and auto_tune is unset', async () => {
    useClusteringStore.getState().addSeedGroup('a', [0, 1, 2])
    useClusteringStore.getState().addSeedGroup('b', [10, 11, 12])
    useClusteringStore.getState().setRegCovar(3.0)
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('', { status: 200, headers: { 'content-type': 'text/event-stream' } })
    )
    vi.stubGlobal('fetch', fetchMock)

    wrap(<FitGMMButton projectId="local" scanId={1} />)
    const btn = screen.getByRole('button', { name: /Fit GMM/ }) as HTMLButtonElement
    fireEvent.click(btn)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body.reg_covar).toBe(3.0)
    expect(body.auto_tune ?? false).toBe(false)
  })
})
