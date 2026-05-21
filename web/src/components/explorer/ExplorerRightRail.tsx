// web/src/components/explorer/ExplorerRightRail.tsx
import { ClusterIncludeExcludePicker } from './ClusterIncludeExcludePicker'
import { NeighborFilterPanel } from './NeighborFilterPanel'
import { RenderTogglesPanel } from './RenderTogglesPanel'
import { LodPicker } from './LodPicker'
import { SaveExplorerStateButton } from './SaveExplorerStateButton'

interface Props {
  projectId: string
  availableLabels: string[]
}

export function ExplorerRightRail({ projectId, availableLabels }: Props) {
  return (
    <aside
      data-testid="explorer-right-rail"
      style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: 8, overflowY: 'auto' }}
    >
      <ClusterIncludeExcludePicker availableLabels={availableLabels} />
      <NeighborFilterPanel />
      <RenderTogglesPanel />
      <LodPicker />
      <SaveExplorerStateButton projectId={projectId} />
    </aside>
  )
}
