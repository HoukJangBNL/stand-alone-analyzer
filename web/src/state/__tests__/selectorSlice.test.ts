import { beforeEach, describe, expect, it } from 'vitest'
import { useSelectorStore } from '@/state/selectorSlice'

describe('selectorSlice', () => {
  beforeEach(() => {
    useSelectorStore.getState().resetFilter()
    useSelectorStore.setState({
      axisX: 'std_r',
      axisY: 'std_g',
      show3D: false,
      brushing: {
        selectedIds: new Set(),
        focusId: null,
        history: [],
        redoStack: [],
      },
      focusDomainId: null,
    })
  })

  it('default filter ranges match metricDefs', () => {
    const s = useSelectorStore.getState()
    expect(s.filter.area).toEqual([0, 1_000_000])
    expect(s.filter.std_r).toEqual([0, 100])
    expect(s.filter.sam2).toEqual([0, 1])
  })

  it('setFilter updates one metric', () => {
    useSelectorStore.getState().setFilter('area', [10, 50_000])
    expect(useSelectorStore.getState().filter.area).toEqual([10, 50_000])
  })

  it('resetFilter restores defaults', () => {
    useSelectorStore.getState().setFilter('area', [10, 50_000])
    useSelectorStore.getState().resetFilter()
    expect(useSelectorStore.getState().filter.area).toEqual([0, 1_000_000])
  })

  it('setAxis updates X or Y independently', () => {
    useSelectorStore.getState().setAxis('X', 'area')
    useSelectorStore.getState().setAxis('Y', 'sam2')
    const s = useSelectorStore.getState()
    expect(s.axisX).toBe('area')
    expect(s.axisY).toBe('sam2')
  })

  it('toUrlParams produces SelectorParams payload (None=null when range == default)', () => {
    useSelectorStore.getState().setFilter('area', [10, 500])
    const p = useSelectorStore.getState().toApiParams()
    expect(p.area_min).toBe(10)
    expect(p.area_max).toBe(500)
    // Untouched metrics must serialize to null/null (= unbounded on backend)
    expect(p.std_r_min).toBeNull()
    expect(p.std_r_max).toBeNull()
  })
})
