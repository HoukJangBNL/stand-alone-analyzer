// web/src/components/clustering/__tests__/ClusteringAxisPicker.test.tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ClusteringAxisPicker } from '@/components/clustering/ClusteringAxisPicker'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('ClusteringAxisPicker', () => {
  it('writes to clusteringSlice (not selectorSlice)', () => {
    render(<ClusteringAxisPicker pane="X" />)
    fireEvent.click(screen.getByLabelText('X: B'))
    expect(useClusteringStore.getState().axisX).toBe('B')
  })

  it('reads its current selection from clusteringSlice', () => {
    useClusteringStore.getState().setAxis('Y', 'std_r')
    render(<ClusteringAxisPicker pane="Y" />)
    expect((screen.getByLabelText('Y: std_r') as HTMLInputElement).checked).toBe(true)
  })
})
