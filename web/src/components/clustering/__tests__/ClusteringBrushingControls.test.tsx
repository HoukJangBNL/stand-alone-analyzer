// web/src/components/clustering/__tests__/ClusteringBrushingControls.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ClusteringBrushingControls } from '@/components/clustering/ClusteringBrushingControls'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('ClusteringBrushingControls', () => {
  it('"Clear brush" empties brushing.selectedIds', () => {
    useClusteringStore.getState().applyLasso([1, 2, 3], 'replace')
    render(<ClusteringBrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Clear/ }))
    expect(useClusteringStore.getState().brushing.selectedIds.size).toBe(0)
  })

  it('Undo reverts last applyLasso', () => {
    useClusteringStore.getState().applyLasso([1, 2], 'replace')
    useClusteringStore.getState().applyLasso([3, 4], 'add')
    render(<ClusteringBrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Undo/ }))
    expect(useClusteringStore.getState().brushing.selectedIds).toEqual(new Set([1, 2]))
  })
})
