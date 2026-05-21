// web/src/components/selector/FilterControls.tsx
import { METRIC_DEFS } from '@/lib/metricDefs'
import { useSelectorStore } from '@/state/selectorSlice'
import { MetricRangeRow } from './MetricRangeRow'

export function FilterControls() {
  const filter = useSelectorStore((s) => s.filter)
  const setFilter = useSelectorStore((s) => s.setFilter)
  const resetFilter = useSelectorStore((s) => s.resetFilter)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <strong>Filter</strong>
        <button onClick={resetFilter}>Reset</button>
      </div>
      {METRIC_DEFS.map((def) => (
        <MetricRangeRow
          key={def.key}
          metricKey={def.key}
          value={filter[def.key]}
          onChange={(next) => setFilter(def.key, next)}
        />
      ))}
    </div>
  )
}
