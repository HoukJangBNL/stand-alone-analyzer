// web/src/components/explorer/ExplorerMain.tsx
import type { TileManifestDto, ExplorerFlakeRowDto } from '@/api/explorer'
import { MosaicCanvas } from './MosaicCanvas'
import { FlakeListPanel } from './FlakeListPanel'
import { DetailPanel } from './DetailPanel'

interface Props {
  projectId: string
  manifest: TileManifestDto
  flakesByStem: Record<string, ExplorerFlakeRowDto[]>
}

export function ExplorerMain({ projectId, manifest, flakesByStem }: Props) {
  return (
    <div
      data-testid="explorer-main-grid"
      style={{
        display: 'grid',
        gridTemplateColumns: '60% 22% 18%',
        gap: 8,
        height: '100%',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <MosaicCanvas manifest={manifest} flakesByStem={flakesByStem} />
      </div>
      <div data-testid="flake-list-panel" style={{ minWidth: 0, overflow: 'auto' }}>
        <FlakeListPanel projectId={projectId} />
      </div>
      <div data-testid="detail-panel-region" style={{ minWidth: 0, overflow: 'auto' }}>
        <DetailPanel projectId={projectId} />
      </div>
    </div>
  )
}
