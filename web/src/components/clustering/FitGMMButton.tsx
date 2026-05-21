import { useClusteringStore } from '@/state/clusteringSlice'
import { useClusteringRefit } from '@/hooks/useClusteringRefit'

interface Props {
  projectId: string
}

export function FitGMMButton({ projectId }: Props) {
  const seedGroups = useClusteringStore((s) => s.seedGroups)
  const fitScope = useClusteringStore((s) => s.fitScope)
  const initialMaxMahalanobis = useClusteringStore((s) => s.initialMaxMahalanobis)
  const refit = useClusteringRefit(projectId)

  const enoughGroups = seedGroups.length >= 2
  const busy = refit.status === 'running'
  const disabled = !enoughGroups || busy

  function handleClick() {
    refit.run({
      seed_groups: seedGroups.map((g) => ({ name: g.name, domain_ids: g.member_ids })),
      fit_scope: fitScope,
      max_mahalanobis: initialMaxMahalanobis,
    })
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <button type="button" onClick={handleClick} disabled={disabled} style={{ padding: '6px 12px' }}>
        Fit GMM{busy ? ` (${Math.round(refit.pct * 100)}%)` : ''}
      </button>
      {refit.status === 'error' && (
        <div role="alert" style={{ color: '#b91c1c', fontSize: 12 }}>
          {refit.message || 'Fit failed'}
        </div>
      )}
      {!enoughGroups && (
        <div style={{ color: '#888', fontSize: 12 }}>Need ≥2 seed groups to fit.</div>
      )}
    </div>
  )
}
