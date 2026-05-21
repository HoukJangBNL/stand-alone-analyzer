import { describe, it, expect, beforeEach } from 'vitest'
import { useSelectorStore } from '@/state/selectorSlice'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

describe('Q-U4: clustering and selector brushing are independent', () => {
  beforeEach(() => {
    useSelectorStore.getState().resetFilter()
    useSelectorStore.getState().clearBrush()
    resetClusteringStore()
  })

  it('mutating selector brushing does not change clustering brushing', () => {
    useSelectorStore.getState().applyLasso([1, 2, 3], 'replace')
    expect(useSelectorStore.getState().brushing.selectedIds).toEqual(new Set([1, 2, 3]))
    expect(useClusteringStore.getState().brushing.selectedIds.size).toBe(0)
  })

  it('mutating clustering brushing does not change selector brushing', () => {
    useClusteringStore.getState().applyLasso([7, 8, 9], 'replace')
    expect(useClusteringStore.getState().brushing.selectedIds).toEqual(new Set([7, 8, 9]))
    expect(useSelectorStore.getState().brushing.selectedIds.size).toBe(0)
  })

  it('focusId is independent', () => {
    useSelectorStore.getState().setFocusId(42)
    expect(useSelectorStore.getState().brushing.focusId).toBe(42)
    expect(useClusteringStore.getState().brushing.focusId).toBeNull()
  })
})
