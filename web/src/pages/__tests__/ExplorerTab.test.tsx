// web/src/pages/__tests__/ExplorerTab.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
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

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
    </MemoryRouter>
  )
}

describe('ExplorerTab page', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
  })

  it('renders the empty-state CTA when prerequisites are missing (409)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: {
              code: 'prerequisite_missing',
              message: 'fit clustering first',
              details: {},
              request_id: 'r',
            },
          }),
          { status: 409, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    wrap(<ExplorerTab projectId="local" scanId={11} />)
    expect(
      await screen.findByText(/Run the Clustering tab to see the Explorer/i)
    ).not.toBeNull()
    expect(screen.getByRole('link', { name: /Open Clustering tab/i })).not.toBeNull()
  })

  it('renders the main grid and right-rail when the manifest loads', async () => {
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
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.includes('/explorer/tile_manifest')) {
          return new Response(manifestBody, {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        return new Response(JSON.stringify({ rows: [], total: 0 }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      })
    )
    wrap(<ExplorerTab projectId="local" scanId={11} />)
    expect(await screen.findByTestId('explorer-main-grid')).not.toBeNull()
    expect(screen.getByTestId('explorer-right-rail')).not.toBeNull()
  })

  it('renders a loading message while the manifest is pending', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise(() => {
            /* never */
          })
      )
    )
    wrap(<ExplorerTab projectId="local" scanId={11} />)
    expect(screen.getByText(/Loading mosaic/i)).not.toBeNull()
  })
})
