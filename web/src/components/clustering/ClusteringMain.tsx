// web/src/components/clustering/ClusteringMain.tsx
import type { DomainStats } from '@/api/selector'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import { ClusterScatterCanvas } from './ClusterScatterCanvas'
import { ClusterSizeBarChart } from './ClusterSizeBarChart'

interface Props {
  stats: DomainStats
  labels: LabelsJson | null
  assignments: AssignmentsRows | null
}

export function ClusteringMain({ stats, labels, assignments }: Props) {
  const fitDone = labels !== null && assignments !== null
  return (
    <div style={{ flex: 1, padding: 12, display: 'flex', flexDirection: 'column', gap: 12, minHeight: 0 }}>
      <ClusterScatterCanvas stats={stats} assignments={assignments} />
      {fitDone && <ClusterSizeBarChart labels={labels} assignments={assignments} />}
    </div>
  )
}
