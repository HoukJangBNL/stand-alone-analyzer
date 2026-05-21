// web/src/state/explorerSlice.ts
// Zustand slice per frontend-design §3.5 + Plan 4 brief.
import { create } from 'zustand'

export type LodChoice = 'auto' | 0 | 1 | 2 | 3

export interface NeighborFilter {
  sizeMin: number | null
  sizeMax: number | null
  isolationMin: number | null
  excludeBorderClipped: boolean
}

export interface ViewportState {
  center: [number, number]
  zoom: number
}

export interface RenderToggles {
  flake_bbox: boolean       // default TRUE
  flake_outline: boolean    // default false
  island_bbox: boolean      // default false
  island_outline: boolean   // default TRUE
}

const DEFAULT_TOGGLES: RenderToggles = {
  flake_bbox: true,
  flake_outline: false,
  island_bbox: false,
  island_outline: true,
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
  lodChoice: LodChoice
  viewportState: ViewportState | null
  renderToggles: RenderToggles

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
  setLodChoice(c: LodChoice): void
  setViewportState(v: ViewportState | null): void
  toggleRender(key: keyof RenderToggles): void
}

export const useExplorerStore = create<ExplorerState>((set) => ({
  includeLabels: new Set<string>(),
  excludeLabels: new Set<string>(),
  neighborFilter: { ...DEFAULT_NEIGHBOR_FILTER },
  selectedFlakeId: null,
  focusFlakeId: null,
  lodChoice: 'auto',
  viewportState: null,
  renderToggles: { ...DEFAULT_TOGGLES },

  addInclude(label) {
    set((s) => {
      const inc = new Set(s.includeLabels)
      inc.add(label)
      const exc = new Set(s.excludeLabels)
      exc.delete(label)
      return { includeLabels: inc, excludeLabels: exc }
    })
  },
  removeInclude(label) {
    set((s) => {
      const inc = new Set(s.includeLabels)
      inc.delete(label)
      return { includeLabels: inc }
    })
  },
  addExclude(label) {
    set((s) => {
      const exc = new Set(s.excludeLabels)
      exc.add(label)
      const inc = new Set(s.includeLabels)
      inc.delete(label)
      return { excludeLabels: exc, includeLabels: inc }
    })
  },
  removeExclude(label) {
    set((s) => {
      const exc = new Set(s.excludeLabels)
      exc.delete(label)
      return { excludeLabels: exc }
    })
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

  setSelectedFlakeId(id) {
    set({ selectedFlakeId: id })
  },
  setFocusFlakeId(id) {
    set({ focusFlakeId: id })
  },
  setLodChoice(c) {
    set({ lodChoice: c })
  },
  setViewportState(v) {
    set({ viewportState: v })
  },
  toggleRender(key) {
    set((s) => ({ renderToggles: { ...s.renderToggles, [key]: !s.renderToggles[key] } }))
  },
}))

export function resetExplorerStore(): void {
  useExplorerStore.setState(
    {
      includeLabels: new Set<string>(),
      excludeLabels: new Set<string>(),
      neighborFilter: { ...DEFAULT_NEIGHBOR_FILTER },
      selectedFlakeId: null,
      focusFlakeId: null,
      lodChoice: 'auto',
      viewportState: null,
      renderToggles: { ...DEFAULT_TOGGLES },
    },
    false
  )
}
