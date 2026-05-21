import { useClusteringStore } from '@/state/clusteringSlice'

export function InitialMahalanobisSlider() {
  const value = useClusteringStore((s) => s.initialMaxMahalanobis)
  const setValue = useClusteringStore((s) => s.setInitialMaxMahalanobis)
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, fontWeight: 600 }}>
        Initial max Mahalanobis ({value.toFixed(2)})
      </span>
      <input
        data-testid="clustering-mahal-initial"
        type="range"
        min={0.5}
        max={6.0}
        step={0.1}
        value={value}
        onChange={(e) => setValue(parseFloat(e.target.value))}
      />
    </label>
  )
}
