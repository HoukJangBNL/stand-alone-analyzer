// web/src/components/explorer/DetailDistance.tsx
interface Props {
  distanceUm: number | null
}

export function DetailDistance({ distanceUm }: Props) {
  if (distanceUm == null) return <div>—</div>
  return (
    <div data-testid="detail-distance">
      Nearest neighbour: {distanceUm.toFixed(2)} µm
    </div>
  )
}
