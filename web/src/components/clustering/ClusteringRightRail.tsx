// web/src/components/clustering/ClusteringRightRail.tsx
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import { SeedGroupEditor } from './SeedGroupEditor'
import { FitScopeRadio } from './FitScopeRadio'
import { InitialMahalanobisSlider } from './InitialMahalanobisSlider'
import { RegCovarSlider } from './RegCovarSlider'
import { FitGMMButton } from './FitGMMButton'
import { AutoTuneButton } from './AutoTuneButton'
import { PerClusterThresholdPanel } from './PerClusterThresholdPanel'
import { LiveMahalanobisSlider } from './LiveMahalanobisSlider'
import { ClusteringBrushingControls } from './ClusteringBrushingControls'
import { ClusteringAxisPicker } from './ClusteringAxisPicker'
import { CommitClusteringButton } from './CommitClusteringButton'

interface Props {
  projectId: string
  scanId: number
  labels: LabelsJson | null
  assignments: AssignmentsRows | null
}

export function ClusteringRightRail({ projectId, scanId, labels, assignments }: Props) {
  const fitDone = labels !== null && assignments !== null
  return (
    <aside style={{ width: 320, borderLeft: '1px solid #eee', padding: 12, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <SeedGroupEditor />
      <FitScopeRadio />
      <InitialMahalanobisSlider />
      <RegCovarSlider />
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        <FitGMMButton projectId={projectId} scanId={scanId} />
        <AutoTuneButton projectId={projectId} scanId={scanId} />
      </div>
      {fitDone && (
        <>
          <PerClusterThresholdPanel labels={labels} assignments={assignments} />
          <LiveMahalanobisSlider />
        </>
      )}
      <ClusteringBrushingControls />
      <ClusteringAxisPicker pane="X" />
      <ClusteringAxisPicker pane="Y" />
      {fitDone && <CommitClusteringButton projectId={projectId} scanId={scanId} />}
    </aside>
  )
}
