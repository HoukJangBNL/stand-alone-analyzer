import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { AutoTuneButton } from '@/components/clustering/AutoTuneButton'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
  vi.unstubAllGlobals()
})

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('<AutoTuneButton>', () => {
  it('submits auto_tune=true and snaps slice regCovar to chosen value on done', async () => {
    useClusteringStore.getState().addSeedGroup('a', [0, 1, 2])
    useClusteringStore.getState().addSeedGroup('b', [10, 11, 12])
    const sseBody =
      'event: done\ndata: ' +
      JSON.stringify({
        result: {
          n_clusters: 2,
          n_assigned: 10,
          n_unassigned: 0,
          output_dir: '/x',
          reg_covar_chosen: 3.0,
        },
      }) +
      '\n\n'
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(sseBody, { status: 200, headers: { 'content-type': 'text/event-stream' } })
    )
    vi.stubGlobal('fetch', fetchMock)

    wrap(<AutoTuneButton projectId="local" />)
    const btn = screen.getByTestId('clustering-auto-tune') as HTMLButtonElement
    fireEvent.click(btn)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
    expect(body.auto_tune).toBe(true)
    await waitFor(() => {
      expect(useClusteringStore.getState().regCovar).toBe(3.0)
    })
  })

  it('is disabled when seedGroups.length < 2', () => {
    wrap(<AutoTuneButton projectId="local" />)
    const btn = screen.getByTestId('clustering-auto-tune') as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })
})
