// Mirrored from backend/tab_selector.py:92-98 (_METRIC_DEFS)
// fmt field uses printf-style format from Streamlit defaults

export type MetricKey = 'area' | 'std_r' | 'std_g' | 'std_b' | 'sam2'

export interface MetricDef {
  key: MetricKey
  label: string
  lo: number
  hi: number
  step: number
  fmt: string
}

export const METRIC_DEFS: readonly MetricDef[] = [
  { key: 'area', label: 'Area (px)', lo: 0, hi: 1_000_000, step: 10, fmt: '%.0f' },
  { key: 'std_r', label: 'Std R %', lo: 0, hi: 100, step: 0.5, fmt: '%.2f' },
  { key: 'std_g', label: 'Std G %', lo: 0, hi: 100, step: 0.5, fmt: '%.2f' },
  { key: 'std_b', label: 'Std B %', lo: 0, hi: 100, step: 0.5, fmt: '%.2f' },
  { key: 'sam2', label: 'SAM2 score', lo: 0, hi: 1, step: 0.05, fmt: '%.2f' },
]

export function defaultRange(key: MetricKey): [number, number] {
  const def = METRIC_DEFS.find((d) => d.key === key)!
  return [def.lo, def.hi]
}
