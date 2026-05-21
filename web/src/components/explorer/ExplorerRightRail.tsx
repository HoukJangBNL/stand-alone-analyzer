// web/src/components/explorer/ExplorerRightRail.tsx
// W3.3: dropped <RenderTogglesPanel> and <LodPicker> (dead in this codebase).
// Surviving controls will be reframed as flake_analyses.curation_params.
import { ClusterIncludeExcludePicker } from './ClusterIncludeExcludePicker'
import { NeighborFilterPanel } from './NeighborFilterPanel'
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
      <SaveExplorerStateButton projectId={projectId} />
    </aside>
  )
}
