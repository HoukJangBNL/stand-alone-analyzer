// web/src/components/explorer/__tests__/DetailPanel.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { DetailPanel } from '../DetailPanel'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe('DetailPanel', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
  })

  it('renders the empty-state message when no flake is selected', () => {
    wrap(<DetailPanel projectId="local" />)
    expect(screen.getByText(/Select a flake to see details/i)).not.toBeNull()
  })

  it('fetches the flake detail and renders identity, labels, and distance', async () => {
    useExplorerStore.getState().setSelectedFlakeId('A:0')
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        flake_id: 'A:0',
        stem: 'A',
        passes_filter: true,
        size_px: 200,
        isolation_um: 4.5,
        nearest_neighbour_um: 7.25,
        cluster_labels: [{ label: 1, name: 'mono' }],
        bbox_norm: [0.1, 0.1, 0.4, 0.4],
        thumbnail_url: '/static/raw/A.jpg',
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<DetailPanel projectId="local" />)
    expect(await screen.findByText('A:0')).not.toBeNull()
    expect(screen.getByText('mono')).not.toBeNull()
    expect(screen.getByText(/7\.25/)).not.toBeNull()
  })

  it('shows a loading message while the query is pending', () => {
    useExplorerStore.getState().setSelectedFlakeId('A:0')
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => { /* never */ })))
    wrap(<DetailPanel projectId="local" />)
    expect(screen.getByText(/Loading detail/i)).not.toBeNull()
  })
})
