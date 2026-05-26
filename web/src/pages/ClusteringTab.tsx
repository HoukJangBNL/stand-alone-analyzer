// web/src/pages/ClusteringTab.tsx
import { useEffect } from 'react'
import { useDomainStats } from '@/hooks/useDomainStats'
import { useClusteringLabels } from '@/hooks/useClusteringLabels'
import { useClusteringAssignments } from '@/hooks/useClusteringAssignments'
import { useClusteringSeedGroups } from '@/hooks/useClusteringSeedGroups'
import { useClusteringStore } from '@/state/clusteringSlice'
import { ClusteringMain } from '@/components/clustering/ClusteringMain'
import { ClusteringRightRail } from '@/components/clustering/ClusteringRightRail'
import { ApiError } from '@/api/selector'

interface Props {
  projectId: string
  scanId?: number | null
}

export function ClusteringTab({ projectId, scanId = null }: Props) {
  if (!scanId) return <p data-testid="clustering-tab-no-scan">Select a scan.</p>
  return <ClusteringTabBody projectId={projectId} scanId={scanId} />
}

function ClusteringTabBody({ projectId, scanId }: { projectId: string; scanId: number }) {
  const stats = useDomainStats(projectId)
  const labels = useClusteringLabels(projectId)
  const assignments = useClusteringAssignments(projectId)
  const seedGroups = useClusteringSeedGroups(projectId)
  const hydrate = useClusteringStore((s) => s.hydrateSeedGroups)

  useEffect(() => {
    if (seedGroups.data) hydrate(seedGroups.data)
  }, [seedGroups.data, hydrate])

  if (stats.isLoading) {
    return <div style={{ padding: 16 }}>Loading domain stats...</div>
  }
  if (stats.error) {
    return (
      <div role="alert" style={{ padding: 16, color: '#b91c1c' }}>
        {(stats.error as Error).message}
      </div>
    )
  }
  if (!stats.data) return null

  const labelsErr = labels.error as ApiError | null
  const assignErr = assignments.error as ApiError | null
  const labelsIs404 = labelsErr instanceof ApiError && labelsErr.status === 404
  const assignIs404 = assignErr instanceof ApiError && assignErr.status === 404

  if (labelsErr && !labelsIs404) {
    return <div role="alert" style={{ padding: 16, color: '#b91c1c' }}>{labelsErr.message}</div>
  }
  if (assignErr && !assignIs404) {
    return <div role="alert" style={{ padding: 16, color: '#b91c1c' }}>{assignErr.message}</div>
  }

  const labelsData = labels.data ?? null
  const assignmentsData = assignments.data ?? null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <ClusteringMain stats={stats.data} labels={labelsData} assignments={assignmentsData} />
        <ClusteringRightRail projectId={projectId} scanId={scanId} labels={labelsData} assignments={assignmentsData} />
      </div>
    </div>
  )
}
