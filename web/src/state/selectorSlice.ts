// web/src/state/selectorSlice.ts
import { create } from 'zustand'
import { defaultRange, METRIC_DEFS, type MetricKey } from '@/lib/metricDefs'
import {
  applyLasso as applyLassoFn,
  clearBrush as clearBrushFn,
  emptyBrushing,
  redo as redoFn,
  setFocusId as setFocusIdFn,
  undo as undoFn,
  type BrushingState,
  type LassoMode,
} from '@/lib/brushing'

export type AvailableAxis = MetricKey | 'R' | 'G' | 'B'

export type FilterRanges = Record<MetricKey, [number, number]>

export interface SelectorApiParams {
  area_min: number | null; area_max: number | null
  std_r_min: number | null; std_r_max: number | null
  std_g_min: number | null; std_g_max: number | null
  std_b_min: number | null; std_b_max: number | null
  sam2_min: number | null; sam2_max: number | null
}

export interface SelectorState {
  filter: FilterRanges
  axisX: AvailableAxis
  axisY: AvailableAxis
  show3D: boolean
  brushing: BrushingState
  focusDomainId: number | null

  setFilter(key: MetricKey, range: [number, number]): void
  resetFilter(): void
  setAxis(pane: 'X' | 'Y', value: AvailableAxis): void
  setShow3D(v: boolean): void

  applyLasso(ids: number[], mode: LassoMode): void
  undoBrush(): void
  redoBrush(): void
  clearBrush(): void
  setFocusId(id: number | null): void

  toApiParams(): SelectorApiParams
}

function buildDefaultFilter(): FilterRanges {
  return {
    area: defaultRange('area'),
    std_r: defaultRange('std_r'),
    std_g: defaultRange('std_g'),
    std_b: defaultRange('std_b'),
    sam2: defaultRange('sam2'),
  }
}

function rangeIsDefault(key: MetricKey, range: [number, number]): boolean {
  const [lo, hi] = defaultRange(key)
  return range[0] === lo && range[1] === hi
}

export const useSelectorStore = create<SelectorState>((set, get) => ({
  filter: buildDefaultFilter(),
  axisX: 'std_r',
  axisY: 'std_g',
  show3D: false,
  brushing: emptyBrushing(),
  focusDomainId: null,

  setFilter(key, range) {
    set((s) => ({ filter: { ...s.filter, [key]: range } }))
  },
  resetFilter() {
    set({ filter: buildDefaultFilter() })
  },
  setAxis(pane, value) {
    set(pane === 'X' ? { axisX: value } : { axisY: value })
  },
  setShow3D(v) {
    set({ show3D: v })
  },

  applyLasso(ids, mode) {
    set((s) => ({ brushing: applyLassoFn(s.brushing, ids, mode) }))
  },
  undoBrush() {
    set((s) => ({ brushing: undoFn(s.brushing) }))
  },
  redoBrush() {
    set((s) => ({ brushing: redoFn(s.brushing) }))
  },
  clearBrush() {
    set((s) => ({ brushing: clearBrushFn(s.brushing) }))
  },
  setFocusId(id) {
    set((s) => ({ brushing: setFocusIdFn(s.brushing, id), focusDomainId: id }))
  },

  toApiParams() {
    const f = get().filter
    const out: SelectorApiParams = {
      area_min: null, area_max: null,
      std_r_min: null, std_r_max: null,
      std_g_min: null, std_g_max: null,
      std_b_min: null, std_b_max: null,
      sam2_min: null, sam2_max: null,
    }
    for (const def of METRIC_DEFS) {
      const range = f[def.key]
      if (rangeIsDefault(def.key, range)) continue
      ;(out as any)[`${def.key}_min`] = range[0]
      ;(out as any)[`${def.key}_max`] = range[1]
    }
    return out
  },
}))
