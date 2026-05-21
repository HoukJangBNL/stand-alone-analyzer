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
    useExplorerStore.getState().setSelectedFlakeId(7)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            flake_id: 7,
            image_id: 42,
            domain_ids: [1, 2],
            cluster_names: ['mono'],
            bbox_xy: [0, 0, 10, 10],
            mask_stats: { area_px: 200 },
            distance_px: 7.25,
            isolation_px: 5.0,
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    wrap(<DetailPanel projectId="local" />)
    expect(await screen.findByText('7')).not.toBeNull()
    expect(screen.getByText('42')).not.toBeNull()
    expect(screen.getByText('mono')).not.toBeNull()
    expect(screen.getByText(/7\.25/)).not.toBeNull()
    expect(screen.getByText(/px/i)).not.toBeNull()
  })

  it('shows a loading message while the query is pending', () => {
    useExplorerStore.getState().setSelectedFlakeId(7)
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => { /* never */ })))
    wrap(<DetailPanel projectId="local" />)
    expect(screen.getByText(/Loading detail/i)).not.toBeNull()
  })

  it('does not render a pass chip (detail Dto has no pass field)', async () => {
    useExplorerStore.getState().setSelectedFlakeId(7)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            flake_id: 7,
            image_id: 42,
            domain_ids: [],
            cluster_names: [],
            bbox_xy: [0, 0, 10, 10],
            mask_stats: {},
            distance_px: null,
            isolation_px: null,
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    wrap(<DetailPanel projectId="local" />)
    await screen.findByText('7')
    expect(screen.queryByTestId('pass-chip')).toBeNull()
  })
})
