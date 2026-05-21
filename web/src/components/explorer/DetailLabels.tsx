// web/src/components/explorer/DetailLabels.tsx
import { CLUSTER_PALETTE } from '@/lib/clusterColors'

interface Props {
  labels: Array<{ label: number; name: string }>
}

export function DetailLabels({ labels }: Props) {
  if (labels.length === 0) return <div>—</div>
  return (
    <div data-testid="detail-labels" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
      {labels.map((l) => {
        const colour = CLUSTER_PALETTE[l.label % CLUSTER_PALETTE.length] ?? '#888'
        return (
          <span
            key={l.label}
            style={{
              background: colour, color: '#fff',
              padding: '2px 6px', borderRadius: 4, fontSize: 12,
            }}
          >
            {l.name}
          </span>
        )
      })}
    </div>
  )
}
