// web/src/components/selector/MetricRangeRow.tsx
/**
 * One row in the filter drawer: label + min/max number inputs.
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
  const { register, watch, setValue, getValues } = useForm<FormShape>({
    defaultValues: { min: value[0], max: value[1] },
  })

  // Stabilize onChange so debounce timer is not reset by parent re-renders.
  const onChangeRef = useRef(onChange)
  useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  // True when the next [min, max] update was driven by an external prop change
  // (e.g. resetFilter). We skip the debounce-commit in that case so prop->form
  // syncs do not feed back into the parent.
  const skipNextCommitRef = useRef(true) // skip the very first effect run on mount

  // Prop -> form sync, only when values actually differ. Mark "skip next commit"
  // so the resulting watch() change does not retrigger onChange.
  useEffect(() => {
    const cur = getValues()
    if (cur.min !== value[0] || cur.max !== value[1]) {
      skipNextCommitRef.current = true
      setValue('min', value[0])
      setValue('max', value[1])
    }
  }, [value, setValue, getValues])

  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const min = watch('min')
  const max = watch('max')

  useEffect(() => {
    if (skipNextCommitRef.current) {
      skipNextCommitRef.current = false
      return
    }
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
      lo = Math.max(def.lo, Math.min(def.hi, lo))
      hi = Math.max(def.lo, Math.min(def.hi, hi))
      onChangeRef.current([lo, hi])
    }, DEBOUNCE_MS)
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current)
    }
  }, [min, max, def.lo, def.hi])

  return (
    <div style={{ marginBottom: 12 }}>
      <label style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>
        {def.label}
      </label>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          data-testid={`selector-metric-${metricKey}-min`}
          aria-label={`${metricKey} min`}
          type="number"
          step={def.step}
          min={def.lo}
          max={def.hi}
          {...register('min', { valueAsNumber: true })}
          style={{ width: '50%' }}
        />
        <input
          data-testid={`selector-metric-${metricKey}-max`}
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
