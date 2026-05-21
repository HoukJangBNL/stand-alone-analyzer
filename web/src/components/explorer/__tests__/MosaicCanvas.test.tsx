// web/src/components/explorer/__tests__/MosaicCanvas.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'

const mockViewer = {
  open: vi.fn(),
  destroy: vi.fn(),
  addOverlay: vi.fn(),
  removeOverlay: vi.fn(),
  clearOverlays: vi.fn(),
  addHandler: vi.fn(),
  world: {
    getItemCount: vi.fn(() => 0),
    getItemAt: vi.fn((_i: number) => ({ setOpacity: vi.fn() })),
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
import type { TileManifestDto, ExplorerFlakeRowDto } from '@/api/explorer'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

const manifest: TileManifestDto = {
  grid_w: 2,
  grid_h: 1,
  lod_sizes: { '0': [256, 256] },
  signature: ['sigA', 'sigB'],
  params_hash: 'hash123',
  tiles: [
    {
      image_id: 10,
      stem: 'A',
      col: 0,
      row: 0,
      width_px: 256,
      height_px: 256,
      lod_sizes: { '0': [256, 256] },
    },
    {
      image_id: 20,
      stem: 'B',
      col: 1,
      row: 0,
      width_px: 256,
      height_px: 256,
      lod_sizes: { '0': [256, 256] },
    },
  ],
}

const flakesByStem: Record<string, ExplorerFlakeRowDto[]> = {
  A: [
    {
      flake_id: 100,
      image_id: 10,
      domains: 1,
      groups: 'mono',
      distance: '5.00 px',
      clipped: 'no',
      pass: true,
    },
  ],
  B: [
    {
      flake_id: 200,
      image_id: 20,
      domains: 1,
      groups: 'bi',
      distance: '2.50 px',
      clipped: 'yes',
      pass: false,
    },
  ],
}

describe('MosaicCanvas', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.clearAllMocks()
  })

  it('mounts an OSD viewer with collectionMode and tileSources from the manifest', () => {
    render(<MosaicCanvas manifest={manifest} flakesByStem={flakesByStem} />)
    expect(OpenSeadragon).toHaveBeenCalledTimes(1)
    const cfg = (OpenSeadragon as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      collectionMode: boolean
      tileSources: Array<{ type: string; url: string; width: number; height: number }>
    }
    expect(cfg.collectionMode).toBe(true)
    expect(cfg.tileSources).toHaveLength(2)
    expect(cfg.tileSources[0]).toMatchObject({
      type: 'image',
      url: '/static/raw/A.jpg',
      width: 256,
      height: 256,
    })
    expect(cfg.tileSources[1]).toMatchObject({
      type: 'image',
      url: '/static/raw/B.jpg',
      width: 256,
      height: 256,
    })
  })

  it('honours grid_w/grid_h by passing collectionRows and collectionColumns', () => {
    render(<MosaicCanvas manifest={manifest} flakesByStem={flakesByStem} />)
    const cfg = (OpenSeadragon as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      collectionTileSize: number
      collectionTileMargin: number
      collectionRows: number
      collectionColumns: number
    }
    expect(cfg.collectionRows).toBe(1)
    expect(cfg.collectionColumns).toBe(2)
  })

  it('allows callers to override the tile-URL builder', () => {
    render(
      <MosaicCanvas
        manifest={manifest}
        flakesByStem={flakesByStem}
        tileUrlBuilder={(t) => `/custom/${t.stem}.png`}
      />
    )
    const cfg = (OpenSeadragon as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0] as {
      tileSources: Array<{ url: string }>
    }
    expect(cfg.tileSources[0].url).toBe('/custom/A.png')
    expect(cfg.tileSources[1].url).toBe('/custom/B.png')
  })

  it('dims fail tiles by setting tile opacity to 0.5', () => {
    let world = 0
    mockViewer.world.getItemCount.mockImplementation(() => world)
    const setOpA = vi.fn()
    const setOpB = vi.fn()
    mockViewer.world.getItemAt.mockImplementation((i: number) =>
      i === 0 ? { setOpacity: setOpA } : { setOpacity: setOpB }
    )
    render(<MosaicCanvas manifest={manifest} flakesByStem={flakesByStem} />)
    const handler = mockViewer.addHandler.mock.calls.find(
      (c: unknown[]) => c[0] === 'open'
    )?.[1] as () => void
    world = 2
    handler?.()
    expect(setOpA).toHaveBeenCalledWith(1)
    expect(setOpB).toHaveBeenCalledWith(0.5)
  })

  it('draws a gold overlay on the selected tile via addOverlay', () => {
    useExplorerStore.getState().setSelectedFlakeId(100)
    render(<MosaicCanvas manifest={manifest} flakesByStem={flakesByStem} />)
    expect(mockViewer.addOverlay).toHaveBeenCalled()
    const call = mockViewer.addOverlay.mock.calls[0][0] as { element: HTMLElement }
    expect(call.element.getAttribute('data-overlay')).toBe('selected-tile')
    expect(call.element.style.outline).toContain('#FFC800')
  })

  it('selects the first flake of the clicked tile on canvas-click', () => {
    render(<MosaicCanvas manifest={manifest} flakesByStem={flakesByStem} />)
    const handler = mockViewer.addHandler.mock.calls.find(
      (c: unknown[]) => c[0] === 'canvas-click'
    )?.[1] as (ev: { position: { x: number; y: number } }) => void
    mockViewer.viewport.viewerElementToViewportCoordinates.mockReturnValueOnce({ x: 0.75, y: 0.5 })
    handler?.({ position: { x: 0, y: 0 } })
    expect(useExplorerStore.getState().selectedFlakeId).toBe(200)
  })

  it('destroys the viewer on unmount', () => {
    const { unmount } = render(<MosaicCanvas manifest={manifest} flakesByStem={flakesByStem} />)
    unmount()
    expect(mockViewer.destroy).toHaveBeenCalled()
  })
})
