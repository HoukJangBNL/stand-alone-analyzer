import { useClusteringStore } from '@/state/clusteringSlice'
import { useClusteringApplyThresholds } from '@/hooks/useClusteringApplyThresholds'

interface Props {
  projectId: string
}

export function CommitClusteringButton({ projectId }: Props) {
  const thresholds = useClusteringStore((s) => s.perClusterThresholds)
  const liveMax = useClusteringStore((s) => s.liveMaxMahalanobis)
  const apply = useClusteringApplyThresholds(projectId)
  const busy = apply.status === 'running'

  function handleClick() {
    apply.run({
      cluster_thresholds: { ...thresholds },
      max_mahalanobis: liveMax,
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <button data-testid="clustering-commit" type="button" onClick={handleClick} disabled={busy} style={{ padding: '6px 12px' }}>
        Commit clustering{busy ? ` (${Math.round(apply.pct * 100)}%)` : ''}
      </button>
      {apply.status === 'done' && apply.result && (
        <div style={{ fontSize: 12, color: '#0f5132' }}>
          {apply.result.n_pass} / {apply.result.n_total} pass across {apply.result.n_clusters} clusters
        </div>
      )}
      {apply.status === 'error' && (
        <div role="alert" style={{ color: '#b91c1c', fontSize: 12 }}>
          {apply.message || 'Commit failed'}
        </div>
      )}
    </div>
  )
}
