// web/src/components/selector/SelectorRightRail.tsx
import type { DomainStats } from '@/api/selector'
import { FilterControls } from './FilterControls'
import { AxisPicker } from './AxisPicker'
import { BrushingControls } from './BrushingControls'
import { Live3DToggle } from './Live3DToggle'
import { LiveCounters } from './LiveCounters'
import { CommitButton } from './CommitButton'

interface Props {
  projectId: string
  stats: DomainStats
}

export function SelectorRightRail({ projectId, stats }: Props) {
  return (
    <aside style={{ width: 280, borderLeft: '1px solid #eee', padding: 12, overflow: 'auto' }}>
      <FilterControls />
      <AxisPicker pane="X" />
      <AxisPicker pane="Y" />
      <BrushingControls />
      <Live3DToggle />
      <LiveCounters stats={stats} />
      <div style={{ marginTop: 12 }}>
        <CommitButton projectId={projectId} />
      </div>
    </aside>
  )
}
