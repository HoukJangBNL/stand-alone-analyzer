// web/src/state/explorerSlice.ts
// Zustand slice. W3.3: removed `lodChoice` + `renderToggles` (state-only no-ops
// with no readers in the production tree). Survivors stay; isolation/border
// fields will map to flake_analyses.curation_params in a future plan.
import { create } from 'zustand'

export interface NeighborFilter {
  sizeMin: number | null
  sizeMax: number | null
  // TODO(flake_analyses): map to curation_params.neighbor_isolation_min
  isolationMin: number | null
  // TODO(flake_analyses): map to curation_params.exclude_border_clipped
  excludeBorderClipped: boolean
}

export interface ViewportState {
  center: [number, number]
  zoom: number
}

const DEFAULT_NEIGHBOR_FILTER: NeighborFilter = {
  sizeMin: null,
  sizeMax: null,
  isolationMin: null,
  excludeBorderClipped: false,
}

export interface ExplorerState {
  includeLabels: Set<string>
  excludeLabels: Set<string>
  neighborFilter: NeighborFilter
  selectedFlakeId: number | null
  focusFlakeId: number | null
  viewportState: ViewportState | null

  addInclude(label: string): void
  removeInclude(label: string): void
  addExclude(label: string): void
  removeExclude(label: string): void
  clearLabels(): void

  setSizeRange(min: number | null, max: number | null): void
  setIsolationMin(v: number | null): void
  setExcludeBorderClipped(v: boolean): void

  setSelectedFlakeId(id: number | null): void
  setFocusFlakeId(id: number | null): void
  setViewportState(v: ViewportState | null): void
}

export const useExplorerStore = create<ExplorerState>((set) => ({
  includeLabels: new Set<string>(),
  excludeLabels: new Set<string>(),
  neighborFilter: { ...DEFAULT_NEIGHBOR_FILTER },
  selectedFlakeId: null,
  focusFlakeId: null,
  viewportState: null,

  addInclude(label) {
    set((s) => {
      const inc = new Set(s.includeLabels); inc.add(label)
      const exc = new Set(s.excludeLabels); exc.delete(label)
      return { includeLabels: inc, excludeLabels: exc }
    })
  },
  removeInclude(label) {
    set((s) => { const inc = new Set(s.includeLabels); inc.delete(label); return { includeLabels: inc } })
  },
  addExclude(label) {
    set((s) => {
      const exc = new Set(s.excludeLabels); exc.add(label)
      const inc = new Set(s.includeLabels); inc.delete(label)
      return { excludeLabels: exc, includeLabels: inc }
    })
  },
  removeExclude(label) {
    set((s) => { const exc = new Set(s.excludeLabels); exc.delete(label); return { excludeLabels: exc } })
  },
  clearLabels() {
    set({ includeLabels: new Set(), excludeLabels: new Set() })
  },

  setSizeRange(min, max) {
    set((s) => ({ neighborFilter: { ...s.neighborFilter, sizeMin: min, sizeMax: max } }))
  },
  setIsolationMin(v) {
    set((s) => ({ neighborFilter: { ...s.neighborFilter, isolationMin: v } }))
  },
  setExcludeBorderClipped(v) {
    set((s) => ({ neighborFilter: { ...s.neighborFilter, excludeBorderClipped: v } }))
  },

  setSelectedFlakeId(id) { set({ selectedFlakeId: id }) },
  setFocusFlakeId(id) { set({ focusFlakeId: id }) },
  setViewportState(v) { set({ viewportState: v }) },
}))

export function resetExplorerStore(): void {
  useExplorerStore.setState(
    {
      includeLabels: new Set<string>(),
      excludeLabels: new Set<string>(),
      neighborFilter: { ...DEFAULT_NEIGHBOR_FILTER },
      selectedFlakeId: null,
      focusFlakeId: null,
      viewportState: null,
    },
    false
  )
}
