// web/src/components/selector/ScatterPanel.tsx
import type { DomainStats } from '@/api/selector'
import { ScatterCanvas } from './ScatterCanvas'

interface Props {
  stats: DomainStats
}

export function ScatterPanel({ stats }: Props) {
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <ScatterCanvas stats={stats} />
    </div>
  )
}
