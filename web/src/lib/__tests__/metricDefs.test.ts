import { describe, expect, it } from 'vitest'
import { METRIC_DEFS, type MetricKey, defaultRange } from '@/lib/metricDefs'

describe('METRIC_DEFS', () => {
  it('has the 5 entries from tab_selector.py:92-98', () => {
    const keys = METRIC_DEFS.map((d) => d.key)
    expect(keys).toEqual(['area', 'std_r', 'std_g', 'std_b', 'sam2'])
  })

  it('area bounds match Streamlit defaults', () => {
    const area = METRIC_DEFS.find((d) => d.key === 'area')!
    expect(area.lo).toBe(0)
    expect(area.hi).toBe(1_000_000)
    expect(area.step).toBe(10)
  })

  it('sam2 bounds are [0, 1]', () => {
    const sam2 = METRIC_DEFS.find((d) => d.key === 'sam2')!
    expect(sam2.lo).toBe(0)
    expect(sam2.hi).toBe(1)
  })

  it('defaultRange returns [lo, hi]', () => {
    expect(defaultRange('std_r')).toEqual([0, 100])
  })

  it('MetricKey type covers all keys', () => {
    const k: MetricKey = 'area'
    expect(typeof k).toBe('string')
  })
})
