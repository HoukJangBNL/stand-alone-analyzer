// web/src/components/explorer/MosaicCanvas.tsx
import { useEffect, useRef } from 'react'
import OpenSeadragon from '@/lib/openseadragon'
import type { TileManifestDto, TileManifestEntryDto, ExplorerFlakeRowDto } from '@/api/explorer'
import { useExplorerStore } from '@/state/explorerSlice'

interface Props {
  manifest: TileManifestDto
  /**
   * Row data joined to the manifest's tiles by `stem`. The parent (Phase 9
   * composer) computes this from `useExplorerFlakes` so MosaicCanvas does not
   * own data fetching.
   */
  flakesByStem?: Record<string, ExplorerFlakeRowDto[]>
  /**
   * Optional URL builder so callers can choose a raw-image extension or
   * substitute a thumbnail route. Defaults to `/static/raw/{stem}.jpg`.
   */
  tileUrlBuilder?: (t: TileManifestEntryDto) => string
}

interface OSDViewerLike {
  open: (...args: unknown[]) => void
  destroy: () => void
  addOverlay: (cfg: { element: HTMLElement; location: unknown }) => void
  removeOverlay: (el: HTMLElement) => void
  clearOverlays: () => void
  addHandler: (name: string, fn: (ev: unknown) => void) => void
  world: {
    getItemCount: () => number
    getItemAt: (i: number) => { setOpacity: (o: number) => void }
  }
  viewport: {
    viewerElementToViewportCoordinates: (p: { x: number; y: number }) => { x: number; y: number }
    viewportToImageCoordinates: (p: { x: number; y: number }) => { x: number; y: number }
  }
  element: HTMLElement
}

const DEFAULT_TILE_URL = (t: TileManifestEntryDto) => `/static/raw/${t.stem}.jpg`

export function MosaicCanvas({ manifest, flakesByStem, tileUrlBuilder }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<OSDViewerLike | null>(null)

  const selectedFlakeId = useExplorerStore((s) => s.selectedFlakeId)
  const setSelectedFlakeId = useExplorerStore((s) => s.setSelectedFlakeId)

  const buildUrl = tileUrlBuilder ?? DEFAULT_TILE_URL
  const flakesMap: Record<string, ExplorerFlakeRowDto[]> = flakesByStem ?? {}

  useEffect(() => {
    if (!containerRef.current) return
    const tileSources = manifest.tiles.map((t) => ({
      type: 'image' as const,
      url: buildUrl(t),
      width: t.width_px,
      height: t.height_px,
      buildPyramid: false,
    }))
    const viewer = OpenSeadragon({
      element: containerRef.current,
      collectionMode: true,
      collectionRows: manifest.grid_h,
      collectionColumns: manifest.grid_w,
      collectionTileSize: 1,
      collectionTileMargin: 0,
      tileSources,
      showNavigator: false,
      gestureSettingsMouse: { scrollToZoom: true, clickToZoom: false },
      crossOriginPolicy: 'Anonymous',
    } as unknown as Parameters<typeof OpenSeadragon>[0]) as unknown as OSDViewerLike
    viewerRef.current = viewer

    viewer.addHandler('open', () => {
      const count = viewer.world.getItemCount()
      for (let i = 0; i < count; i++) {
        const tile = manifest.tiles[i]
        if (!tile) continue
        const flakes = flakesMap[tile.stem] ?? []
        const allFail = flakes.length > 0 && flakes.every((f) => !f.pass)
        viewer.world.getItemAt(i).setOpacity(allFail ? 0.5 : 1)
      }
    })

    viewer.addHandler('canvas-click', (raw) => {
      const ev = raw as { position: { x: number; y: number } }
      const vp = viewer.viewport.viewerElementToViewportCoordinates(ev.position)
      const col = Math.min(manifest.grid_w - 1, Math.max(0, Math.floor(vp.x * manifest.grid_w)))
      const row = Math.min(manifest.grid_h - 1, Math.max(0, Math.floor(vp.y * manifest.grid_h)))
      const tile = manifest.tiles.find((t) => t.col === col && t.row === row)
      if (!tile) return
      const first = (flakesMap[tile.stem] ?? [])[0]
      if (first) setSelectedFlakeId(first.flake_id)
    })

    return () => {
      viewer.destroy()
      viewerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest, flakesByStem])

  // Selected-tile gold overlay: re-render when selection changes.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    viewer.clearOverlays()
    if (selectedFlakeId === null) return
    const tile = manifest.tiles.find((t) =>
      (flakesMap[t.stem] ?? []).some((f) => f.flake_id === selectedFlakeId)
    )
    if (!tile) return
    const el = document.createElement('div')
    el.setAttribute('data-overlay', 'selected-tile')
    el.style.outline = '3px solid #FFC800'
    el.style.boxSizing = 'border-box'
    el.style.pointerEvents = 'none'
    viewer.addOverlay({
      element: el,
      location: { x: tile.col, y: tile.row, width: 1, height: 1 },
    })
  }, [selectedFlakeId, manifest, flakesByStem])

  return (
    <div
      ref={containerRef}
      data-testid="mosaic-canvas"
      style={{ width: '100%', height: '100%', minHeight: '400px', background: '#000' }}
    />
  )
}
