// web/src/components/explorer/DetailPanel.tsx
import { useExplorerFlakeDetail } from '@/hooks/useExplorerFlakeDetail'
import { useExplorerStore } from '@/state/explorerSlice'
import { DetailIdentity } from './DetailIdentity'
import { DetailLabels } from './DetailLabels'
import { DetailDistance } from './DetailDistance'

interface Props {
  projectId: string
}

export function DetailPanel({ projectId }: Props) {
  const flakeId = useExplorerStore((s) => s.selectedFlakeId)
  const { data, isLoading, isError } = useExplorerFlakeDetail(projectId, flakeId)

  if (!flakeId) return <div>Select a flake to see details.</div>
  if (isLoading) return <div>Loading detail...</div>
  if (isError || !data) return <div>Failed to load flake detail.</div>

  return (
    <div data-testid="detail-panel" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <DetailIdentity flakeId={data.flake_id} stem={data.stem} passes={data.passes_filter} />
      <DetailLabels labels={data.cluster_labels} />
      <DetailDistance distanceUm={data.nearest_neighbour_um} />
      {data.thumbnail_url && (
        <img
          src={data.thumbnail_url}
          alt={data.flake_id}
          data-testid="detail-thumbnail"
          style={{ maxWidth: '100%', height: 'auto' }}
        />
      )}
    </div>
  )
}
