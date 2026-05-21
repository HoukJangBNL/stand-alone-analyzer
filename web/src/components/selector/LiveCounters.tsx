// web/src/components/selector/LiveCounters.tsx
import { useMemo } from 'react'
import { useSelectorStore } from '@/state/selectorSlice'
import { computeAccepted } from '@/lib/applyFilter'
import type { DomainStats } from '@/api/selector'

interface LiveCountersProps {
  stats: DomainStats
}

export function LiveCounters({ stats }: LiveCountersProps) {
  const filter = useSelectorStore((s) => s.filter)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)

  const counts = useMemo(() => {
    const accepted = computeAccepted(stats, filter)
    const total = stats.flake_ids.length
    const selectedCount = selectedIds.size
    let willCommit = 0
    if (selectedCount === 0) {
      willCommit = accepted.size
    } else {
      for (const id of selectedIds) if (accepted.has(id)) willCommit++
    }
    return {
      accepted: accepted.size,
      rejected: total - accepted.size,
      selected: selectedCount,
      willCommit,
    }
  }, [stats, filter, selectedIds])

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 4, fontSize: 12 }}>
      <div data-testid="counter-accepted">Accepted: <strong>{counts.accepted}</strong></div>
      <div data-testid="counter-rejected">Rejected: <strong>{counts.rejected}</strong></div>
      <div data-testid="counter-selected">Selected: <strong>{counts.selected}</strong></div>
      <div data-testid="counter-will-commit">Will commit: <strong>{counts.willCommit}</strong></div>
    </div>
  )
}
