import { useClusteringStore } from '@/state/clusteringSlice'
import { logToValue, valueToLog } from '@/lib/logScale'

const MIN = 0.1
const MAX = 10.0

export function RegCovarSlider() {
  const value = useClusteringStore((s) => s.regCovar)
  const setValue = useClusteringStore((s) => s.setRegCovar)
  const t = valueToLog(value, MIN, MAX)
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, fontWeight: 600 }}>
        Cov reg (
        <span data-testid="clustering-reg-covar-value">{value.toFixed(2)}</span>
        )
      </span>
      <input
        data-testid="clustering-reg-covar-slider"
        type="range"
        min={0}
        max={1}
        step={0.001}
        value={t}
        onChange={(e) => setValue(logToValue(parseFloat(e.target.value), MIN, MAX))}
      />
    </label>
  )
}
