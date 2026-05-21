// web/src/components/explorer/DetailLabels.tsx
import { CLUSTER_PALETTE } from '@/lib/clusterColors'

interface Props {
  /**
   * Cluster names from ExplorerFlakeDetailDto.cluster_names. The detail Dto
   * does not carry numeric labels, so we colour by index-mod-palette.
   */
  names: string[]
}

export function DetailLabels({ names }: Props) {
  if (names.length === 0) return <div>—</div>
  return (
    <div data-testid="detail-labels" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {names.map((n, i) => {
        const colour = CLUSTER_PALETTE[i % CLUSTER_PALETTE.length] ?? '#888'
        return (
          <span
            key={`${n}-${i}`}
            style={{
              background: colour, color: '#fff',
              padding: '2px 6px', borderRadius: 4, fontSize: 12,
            }}
          >
            {n}
          </span>
        )
      })}
    </div>
  )
}
