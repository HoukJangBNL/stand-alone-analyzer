// web/src/components/clustering/ClusteringBrushingControls.tsx
import { useClusteringStore } from '@/state/clusteringSlice'

export function ClusteringBrushingControls() {
  const undo = useClusteringStore((s) => s.undoBrush)
  const redo = useClusteringStore((s) => s.redoBrush)
  const clear = useClusteringStore((s) => s.clearBrush)
  const count = useClusteringStore((s) => s.brushing.selectedIds.size)
  return (
    <div style={{ display: 'flex', gap: 4, alignItems: 'center', margin: '8px 0' }}>
      <span style={{ fontSize: 12, color: '#444' }}>Brush ({count})</span>
      <button data-testid="clustering-brushing-undo" type="button" onClick={undo} style={{ fontSize: 12 }}>Undo</button>
      <button data-testid="clustering-brushing-redo" type="button" onClick={redo} style={{ fontSize: 12 }}>Redo</button>
      <button data-testid="clustering-brushing-clear" type="button" onClick={clear} style={{ fontSize: 12 }}>Clear</button>
    </div>
  )
}
