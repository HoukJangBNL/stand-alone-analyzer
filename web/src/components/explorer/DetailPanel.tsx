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

  if (flakeId === null) return <div>Select a flake to see details.</div>
  if (isLoading) return <div>Loading detail...</div>
  if (isError || !data) return <div>Failed to load flake detail.</div>

  return (
    <div data-testid="detail-panel" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/*
        ExplorerFlakeDetailDto has no `pass` or `thumbnail_url` field — the
        pass chip is omitted here and the thumbnail is rendered elsewhere
        when needed. Stem is also absent from detail; identity is keyed by
        flake_id + image_id.
      */}
      <DetailIdentity flakeId={data.flake_id} imageId={data.image_id} />
      <DetailLabels names={data.cluster_names} />
      <DetailDistance distancePx={data.distance_px} />
    </div>
  )
}
