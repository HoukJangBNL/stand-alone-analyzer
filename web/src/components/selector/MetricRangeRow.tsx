// web/src/components/selector/MetricRangeRow.tsx
/**
 * One row in the filter drawer: label + slider + min/max number inputs.
 * RHF holds the working values; we commit (with 200ms debounce) to the
 * caller's onChange so Zustand updates only at rest.
 *
 * Ports the swap-on-cross behaviour from tab_selector.py:181-184.
 */
import { useEffect, useRef } from 'react'
import { useForm } from 'react-hook-form'
import { METRIC_DEFS, type MetricKey } from '@/lib/metricDefs'

interface MetricRangeRowProps {
  metricKey: MetricKey
  value: [number, number]
  onChange(next: [number, number]): void
}

interface FormShape {
  min: number
  max: number
}

const DEBOUNCE_MS = 200

export function MetricRangeRow({ metricKey, value, onChange }: MetricRangeRowProps) {
  const def = METRIC_DEFS.find((d) => d.key === metricKey)!
  const { register, watch, setValue } = useForm<FormShape>({
    defaultValues: { min: value[0], max: value[1] },
  })

  // Sync external prop -> form when it changes from outside (e.g. resetFilter).
  useEffect(() => {
    setValue('min', value[0])
    setValue('max', value[1])
  }, [value, setValue])

  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const min = watch('min')
  const max = watch('max')

  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => {
      let lo = Number(min)
      let hi = Number(max)
      if (Number.isNaN(lo) || Number.isNaN(hi)) return
      if (lo > hi) {
        const swap = lo
        lo = hi
        hi = swap
      }
      // Clamp to def range
      lo = Math.max(def.lo, Math.min(def.hi, lo))
      hi = Math.max(def.lo, Math.min(def.hi, hi))
      onChange([lo, hi])
    }, DEBOUNCE_MS)
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
    }
  }, [min, max, def.lo, def.hi, onChange])

  return (
    <div style={{ marginBottom: 12 }}>
      <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>
        {def.label}
      </label>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          aria-label={`${metricKey} min`}
          type="number"
          step={def.step}
          min={def.lo}
          max={def.hi}
          {...register('min', { valueAsNumber: true })}
          style={{ width: '50%' }}
        />
        <input
          aria-label={`${metricKey} max`}
          type="number"
          step={def.step}
          min={def.lo}
          max={def.hi}
          {...register('max', { valueAsNumber: true })}
          style={{ width: '50%' }}
        />
      </div>
    </div>
  )
}
