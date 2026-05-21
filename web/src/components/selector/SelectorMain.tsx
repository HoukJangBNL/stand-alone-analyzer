// web/src/components/selector/SelectorMain.tsx
import type { DomainStats } from '@/api/selector'
import { useSelectorStore } from '@/state/selectorSlice'
import { ScatterPanel } from './ScatterPanel'
import { ImagePreviewPanel } from './ImagePreviewPanel'
import { RGBScatter3DPanel } from './RGBScatter3DPanel'

interface Props {
  projectId: string
  stats: DomainStats
}

export function SelectorMain({ projectId, stats }: Props) {
  const show3D = useSelectorStore((s) => s.show3D)
  return (
    <div style={{ flex: 1, padding: 12, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 12, minHeight: 0 }}>
        <ScatterPanel stats={stats} />
        <ImagePreviewPanel projectId={projectId} />
      </div>
      {show3D && <RGBScatter3DPanel stats={stats} />}
    </div>
  )
}
