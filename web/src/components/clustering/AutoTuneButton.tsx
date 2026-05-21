import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { useClusteringStore } from '@/state/clusteringSlice'
import { useClusteringRefit } from '@/hooks/useClusteringRefit'

interface Props {
  projectId: string
}

type RefitStatus = 'idle' | 'running' | 'done' | 'error'

export function AutoTuneButton({ projectId }: Props) {
  const seedGroups = useClusteringStore((s) => s.seedGroups)
  const fitScope = useClusteringStore((s) => s.fitScope)
  const initialMaxMahalanobis = useClusteringStore((s) => s.initialMaxMahalanobis)
  const setRegCovar = useClusteringStore((s) => s.setRegCovar)
  const refit = useClusteringRefit(projectId)
  const lastSeenStatus = useRef<RefitStatus>('idle')

  useEffect(() => {
    if (refit.status === 'done' && lastSeenStatus.current !== 'done') {
      const chosen = (refit.result as { reg_covar_chosen?: number } | null)?.reg_covar_chosen
      if (typeof chosen === 'number') {
        setRegCovar(chosen)
        toast.success(`Auto-tuned reg_covar = ${chosen.toFixed(2)}`)
      }
    }
    lastSeenStatus.current = refit.status
  }, [refit.status, refit.result, setRegCovar])

  const enoughGroups = seedGroups.length >= 2
  const busy = refit.status === 'running'
  const disabled = !enoughGroups || busy

  function handleClick() {
    refit.run({
      seed_groups: seedGroups.map((g) => ({ name: g.name, domain_ids: g.member_ids })),
      fit_scope: fitScope,
      max_mahalanobis: initialMaxMahalanobis,
      auto_tune: true,
    })
  }

  return (
    <button
      data-testid="clustering-auto-tune"
      type="button"
      onClick={handleClick}
      disabled={disabled}
      style={{ padding: '6px 12px' }}
    >
      Auto-tune{busy ? ` (${Math.round(refit.pct * 100)}%)` : ''}
    </button>
  )
}
