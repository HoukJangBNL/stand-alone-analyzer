import { useClusteringStore } from '@/state/clusteringSlice'

export function LiveMahalanobisSlider() {
  const value = useClusteringStore((s) => s.liveMaxMahalanobis)
  const setValue = useClusteringStore((s) => s.setLiveMaxMahalanobis)
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, fontWeight: 600 }}>
        Live max Mahalanobis ({value.toFixed(2)})
      </span>
      <input
        type="range"
        min={0.5}
        max={8.0}
        step={0.1}
        value={value}
        onChange={(e) => setValue(parseFloat(e.target.value))}
      />
    </label>
  )
}
