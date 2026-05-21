// web/src/components/explorer/DetailDistance.tsx
interface Props {
  /**
   * Pixel distance to the nearest neighbour, from
   * ExplorerFlakeDetailDto.distance_px. The backend reports pixels, not µm.
   */
  distancePx: number | null
}

export function DetailDistance({ distancePx }: Props) {
  if (distancePx == null) return <div>—</div>
  return (
    <div data-testid="detail-distance">
      Nearest neighbour: {distancePx.toFixed(2)} px
    </div>
  )
}
