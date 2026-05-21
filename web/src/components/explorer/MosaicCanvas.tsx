// web/src/components/explorer/MosaicCanvas.tsx
import { useEffect, useRef } from 'react'
import OpenSeadragon from '@/lib/openseadragon'
import type { TileManifest } from '@/api/explorer'
import { useExplorerStore } from '@/state/explorerSlice'

interface Props {
  manifest: TileManifest
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

export function MosaicCanvas({ manifest }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const viewerRef = useRef<OSDViewerLike | null>(null)

  const selectedFlakeId = useExplorerStore((s) => s.selectedFlakeId)
  const setSelectedFlakeId = useExplorerStore((s) => s.setSelectedFlakeId)

  useEffect(() => {
    if (!containerRef.current) return
    const tileSources = manifest.tiles.map((t) => ({
      type: 'image' as const,
      url: t.url,
      width: t.w,
      height: t.h,
      buildPyramid: false,
    }))
    const viewer = OpenSeadragon({
      element: containerRef.current,
      collectionMode: true,
      collectionRows: manifest.rows,
      collectionColumns: manifest.cols,
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
        const flakes = manifest.flakes_by_stem[tile.stem] ?? []
        const allFail = flakes.length > 0 && flakes.every((f) => !f.passes_filter)
        viewer.world.getItemAt(i).setOpacity(allFail ? 0.5 : 1)
      }
    })

    viewer.addHandler('canvas-click', (raw) => {
      const ev = raw as { position: { x: number; y: number } }
      const vp = viewer.viewport.viewerElementToViewportCoordinates(ev.position)
      const col = Math.min(manifest.cols - 1, Math.max(0, Math.floor(vp.x * manifest.cols)))
      const row = Math.min(manifest.rows - 1, Math.max(0, Math.floor(vp.y * manifest.rows)))
      const tile = manifest.tiles.find((t) => t.col === col && t.row === row)
      if (!tile) return
      const first = (manifest.flakes_by_stem[tile.stem] ?? [])[0]
      if (first) setSelectedFlakeId(first.flake_id)
    })

    return () => {
      viewer.destroy()
      viewerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifest])

  // Selected-tile gold overlay: re-render when selection changes.
  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    viewer.clearOverlays()
    if (!selectedFlakeId) return
    const tile = manifest.tiles.find((t) =>
      (manifest.flakes_by_stem[t.stem] ?? []).some((f) => f.flake_id === selectedFlakeId)
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
  }, [selectedFlakeId, manifest])

  return (
    <div
      ref={containerRef}
      data-testid="mosaic-canvas"
      style={{ width: '100%', height: '100%', minHeight: '400px', background: '#000' }}
    />
  )
}
