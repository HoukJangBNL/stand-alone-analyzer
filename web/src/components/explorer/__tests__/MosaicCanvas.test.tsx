// web/src/components/explorer/__tests__/MosaicCanvas.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import React from 'react'

const mockViewer = {
  open: vi.fn(),
  destroy: vi.fn(),
  addOverlay: vi.fn(),
  removeOverlay: vi.fn(),
  clearOverlays: vi.fn(),
  addHandler: vi.fn(),
  world: {
    getItemCount: vi.fn(() => 0),
    getItemAt: vi.fn(() => ({ setOpacity: vi.fn() })),
  },
  viewport: {
    viewerElementToViewportCoordinates: vi.fn(() => ({ x: 0.05, y: 0.05 })),
    viewportToImageCoordinates: vi.fn(() => ({ x: 100, y: 100 })),
  },
  element: document.createElement('div'),
}

vi.mock('openseadragon', () => ({
  default: vi.fn(() => mockViewer),
}))

import OpenSeadragon from 'openseadragon'
import { MosaicCanvas } from '../MosaicCanvas'
import type { TileManifest } from '@/api/explorer'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

const manifest: TileManifest = {
  project_id: 'local',
  cols: 2,
  rows: 1,
  tile_w_px: 256,
  tile_h_px: 256,
  pyramid: { lod_choice: 'auto', cache_dir: '/x', available_lods: [0] },
  tiles: [
    { stem: 'A', col: 0, row: 0, url: '/static/raw/A.jpg', w: 256, h: 256, lod: null },
    { stem: 'B', col: 1, row: 0, url: '/static/raw/B.jpg', w: 256, h: 256, lod: null },
  ],
  flakes_by_stem: {
    A: [{ flake_id: 'A:0', cluster_label: 1, passes_filter: true, bbox_norm: [0.1, 0.1, 0.4, 0.4] }],
    B: [{ flake_id: 'B:0', cluster_label: 2, passes_filter: false, bbox_norm: [0.2, 0.2, 0.5, 0.5] }],
  },
}

describe('MosaicCanvas', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.clearAllMocks()
  })

  it('mounts an OSD viewer with collectionMode and tileSources from the manifest', () => {
    render(<MosaicCanvas manifest={manifest} />)
    expect(OpenSeadragon).toHaveBeenCalledTimes(1)
    const cfg = (OpenSeadragon as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      collectionMode: boolean
      tileSources: Array<{ type: string; url: string; width: number; height: number }>
    }
    expect(cfg.collectionMode).toBe(true)
    expect(cfg.tileSources).toHaveLength(2)
    expect(cfg.tileSources[0]).toMatchObject({ type: 'image', url: '/static/raw/A.jpg' })
    expect(cfg.tileSources[1]).toMatchObject({ type: 'image', url: '/static/raw/B.jpg' })
  })

  it('honours server-side Y-flip by passing tile rows in collectionLayout', () => {
    render(<MosaicCanvas manifest={manifest} />)
    const cfg = (OpenSeadragon as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      collectionTileSize: number
      collectionTileMargin: number
      collectionRows: number
      collectionColumns: number
    }
    expect(cfg.collectionRows).toBe(1)
    expect(cfg.collectionColumns).toBe(2)
  })

  it('dims fail tiles by setting tile opacity to 0.5', () => {
    let world = 0
    mockViewer.world.getItemCount.mockImplementation(() => world)
    const setOpA = vi.fn()
    const setOpB = vi.fn()
    mockViewer.world.getItemAt.mockImplementation((i: number) =>
      i === 0 ? { setOpacity: setOpA } : { setOpacity: setOpB }
    )
    render(<MosaicCanvas manifest={manifest} />)
    const handler = mockViewer.addHandler.mock.calls.find(
      (c: unknown[]) => c[0] === 'open'
    )?.[1] as () => void
    world = 2
    handler?.()
    expect(setOpA).toHaveBeenCalledWith(1)
    expect(setOpB).toHaveBeenCalledWith(0.5)
  })

  it('draws a gold overlay on the selected tile via addOverlay', () => {
    useExplorerStore.getState().setSelectedFlakeId('A:0')
    render(<MosaicCanvas manifest={manifest} />)
    expect(mockViewer.addOverlay).toHaveBeenCalled()
    const call = mockViewer.addOverlay.mock.calls[0][0] as { element: HTMLElement }
    expect(call.element.getAttribute('data-overlay')).toBe('selected-tile')
    expect(call.element.style.outline).toContain('#FFC800')
  })

  it('selects the first flake of the clicked tile on canvas-click', () => {
    render(<MosaicCanvas manifest={manifest} />)
    const handler = mockViewer.addHandler.mock.calls.find(
      (c: unknown[]) => c[0] === 'canvas-click'
    )?.[1] as (ev: { position: { x: number; y: number } }) => void
    mockViewer.viewport.viewerElementToViewportCoordinates.mockReturnValueOnce({ x: 0.75, y: 0.5 })
    handler?.({ position: { x: 0, y: 0 } })
    expect(useExplorerStore.getState().selectedFlakeId).toBe('B:0')
  })

  it('destroys the viewer on unmount', () => {
    const { unmount } = render(<MosaicCanvas manifest={manifest} />)
    unmount()
    expect(mockViewer.destroy).toHaveBeenCalled()
  })
})
