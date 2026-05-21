// web/src/components/explorer/__tests__/ExplorerMain.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { ExplorerMain } from '../ExplorerMain'
import type { TileManifestDto } from '@/api/explorer'
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
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

const manifest: TileManifestDto = {
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
}

describe('ExplorerMain', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ rows: [], total: 0 }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    )
  })

  it('lays out the three columns with grid template "60% 22% 18%"', () => {
    wrap(<ExplorerMain projectId="local" manifest={manifest} flakesByStem={{}} />)
    const grid = screen.getByTestId('explorer-main-grid')
    expect(grid.style.gridTemplateColumns).toBe('60% 22% 18%')
  })

  it('renders the mosaic, the flake-list panel region, and the detail panel region', () => {
    wrap(<ExplorerMain projectId="local" manifest={manifest} flakesByStem={{}} />)
    expect(screen.getByTestId('mosaic-canvas')).not.toBeNull()
    expect(screen.getByTestId('flake-list-panel')).not.toBeNull()
    expect(screen.getByTestId('detail-panel-region')).not.toBeNull()
  })
})
