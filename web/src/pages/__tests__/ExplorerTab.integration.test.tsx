// web/src/pages/__tests__/ExplorerTab.integration.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import React from 'react'
import { ExplorerTab } from '../ExplorerTab'
import { resetExplorerStore } from '@/state/explorerSlice'

vi.mock('openseadragon', () => ({
  default: vi.fn(() => ({
    open: vi.fn(),
    destroy: vi.fn(),
    addOverlay: vi.fn(),
    removeOverlay: vi.fn(),
    clearOverlays: vi.fn(),
    addHandler: vi.fn(),
    world: { getItemCount: () => 0, getItemAt: () => ({ setOpacity: vi.fn() }) },
    viewport: {
      viewerElementToViewportCoordinates: () => ({ x: 0, y: 0 }),
      viewportToImageCoordinates: () => ({ x: 0, y: 0 }),
    },
    element: document.createElement('div'),
  })),
}))

const mockToastSuccess = vi.fn()
vi.mock('sonner', () => ({
  toast: {
    success: (...args: unknown[]) => mockToastSuccess(...args),
    error: vi.fn(),
  },
}))

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
    </MemoryRouter>
  )
}

describe('ExplorerTab integration — happy path', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
    mockToastSuccess.mockClear()
  })

  it('manifest → flake-list row click → detail loads → save mutation succeeds and toasts', async () => {
    const manifestBody = JSON.stringify({
      grid_w: 1,
      grid_h: 1,
      lod_sizes: {},
      signature: [],
      params_hash: 'p',
      tiles: [
        {
          image_id: 1,
          stem: 'A',
          col: 0,
          row: 0,
          width_px: 256,
          height_px: 256,
          lod_sizes: {},
        },
      ],
    })
    const flakesBody = JSON.stringify({
      rows: [
        {
          flake_id: 0,
          image_id: 1,
          domains: 1,
          groups: 'mono',
          distance: '7.25',
          clipped: 'no',
          pass: true,
        },
      ],
      total: 1,
    })
    const flakeBody = JSON.stringify({
      flake_id: 0,
      image_id: 1,
      domain_ids: [1],
      cluster_names: ['mono'],
      bbox_xy: [0, 0, 1, 1],
      mask_stats: {},
      distance_px: 7.25,
      isolation_px: 4.5,
    })
    const saveBody = JSON.stringify({
      state_path: '/proj/explorer_state.npz',
      selected_count: 1,
    })

    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url.includes('/explorer/tile_manifest')) {
          return new Response(manifestBody, {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        if (url.includes('/explorer/flake/')) {
          return new Response(flakeBody, {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        if (url.includes('/explorer/flakes')) {
          return new Response(flakesBody, {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        if (url.includes('/run/explorer/save_state') && init?.method === 'POST') {
          return new Response(saveBody, {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        return new Response('{}', {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      })
    )

    wrap(<ExplorerTab projectId="local" />)

    // 1. Manifest loads, mosaic + right-rail render.
    await screen.findByTestId('explorer-main-grid')
    expect(screen.getByTestId('explorer-right-rail')).not.toBeNull()

    // 2. Flake-list row appears (first column shows flake_id "0"); click it.
    const row = await screen.findByText('0')
    fireEvent.click(row)

    // 3. Detail panel resolves with cluster name + distance. Scope to the
    // detail-labels region so we don't collide with the same text appearing in
    // the flake-list row and the cluster-include/exclude pickers.
    const detailLabels = await screen.findByTestId('detail-labels')
    await waitFor(() => expect(detailLabels.textContent).toContain('mono'))
    const detailDistance = screen.getByTestId('detail-distance')
    expect(detailDistance.textContent).toMatch(/7\.25/)

    // 4. Save button → success toast.
    fireEvent.click(screen.getByRole('button', { name: /Save Explorer state/i }))
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalled())
  })
})
