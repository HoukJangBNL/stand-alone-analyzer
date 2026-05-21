import { create } from 'zustand'
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
import type { AvailableAxis } from '@/state/selectorSlice'

export interface SeedGroup {
  id: string
  name: string
  member_ids: number[]
}

export type FitScope = 'seeds' | 'all_selected'

export interface ClusteringState {
  seedGroups: SeedGroup[]
  fitScope: FitScope
  initialMaxMahalanobis: number
  liveMaxMahalanobis: number
  perClusterThresholds: Record<number, number>
  axisX: AvailableAxis
  axisY: AvailableAxis
  brushing: BrushingState
  editingGroupId: string | null

  addSeedGroup(name: string, memberIds: number[]): void
  renameSeedGroup(id: string, name: string): void
  removeSeedGroup(id: string): void
  clearSeedGroups(): void
  hydrateSeedGroups(disk: Array<{ name: string; domain_ids: number[] }>): void

  setThreshold(clusterId: number, value: number): void
  resetThresholdsToDefault(): void

  setFitScope(scope: FitScope): void
  setInitialMaxMahalanobis(v: number): void
  setLiveMaxMahalanobis(v: number): void

  setAxis(pane: 'X' | 'Y', value: AvailableAxis): void
  setEditingGroupId(id: string | null): void

  applyLasso(ids: number[], mode: LassoMode): void
  undoBrush(): void
  redoBrush(): void
  clearBrush(): void
  setFocusId(id: number | null): void
}

let _seedIdCounter = 0
function nextSeedId(): string {
  _seedIdCounter += 1
  return `sg-${_seedIdCounter}`
}

export const useClusteringStore = create<ClusteringState>((set, get) => ({
  seedGroups: [],
  fitScope: 'seeds',
  initialMaxMahalanobis: 3.0,
  liveMaxMahalanobis: 3.0,
  perClusterThresholds: {},
  axisX: 'R',
  axisY: 'G',
  brushing: emptyBrushing(),
  editingGroupId: null,

  addSeedGroup(name, memberIds) {
    set((s) => ({
      seedGroups: [...s.seedGroups, { id: nextSeedId(), name, member_ids: [...memberIds] }],
    }))
  },
  renameSeedGroup(id, name) {
    set((s) => ({
      seedGroups: s.seedGroups.map((g) => (g.id === id ? { ...g, name } : g)),
    }))
  },
  removeSeedGroup(id) {
    set((s) => ({ seedGroups: s.seedGroups.filter((g) => g.id !== id) }))
  },
  clearSeedGroups() {
    set({ seedGroups: [], editingGroupId: null })
  },
  hydrateSeedGroups(disk) {
    if (get().seedGroups.length > 0) return
    set({
      seedGroups: disk.map((d) => ({
        id: nextSeedId(),
        name: d.name,
        member_ids: [...d.domain_ids],
      })),
    })
  },

  setThreshold(clusterId, value) {
    set((s) => ({
      perClusterThresholds: { ...s.perClusterThresholds, [clusterId]: value },
    }))
  },
  resetThresholdsToDefault() {
    set({ perClusterThresholds: {} })
  },

  setFitScope(scope) {
    set({ fitScope: scope })
  },
  setInitialMaxMahalanobis(v) {
    set({ initialMaxMahalanobis: v })
  },
  setLiveMaxMahalanobis(v) {
    set({ liveMaxMahalanobis: v })
  },

  setAxis(pane, value) {
    set(pane === 'X' ? { axisX: value } : { axisY: value })
  },
  setEditingGroupId(id) {
    set({ editingGroupId: id })
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
    set((s) => ({ brushing: setFocusIdFn(s.brushing, id) }))
  },
}))

export function resetClusteringStore(): void {
  _seedIdCounter = 0
  useClusteringStore.setState(
    {
      seedGroups: [],
      fitScope: 'seeds',
      initialMaxMahalanobis: 3.0,
      liveMaxMahalanobis: 3.0,
      perClusterThresholds: {},
      axisX: 'R',
      axisY: 'G',
      brushing: emptyBrushing(),
      editingGroupId: null,
    },
    false
  )
}
