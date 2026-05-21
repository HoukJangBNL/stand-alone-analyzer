// web/src/components/selector/__tests__/BrushingControls.test.tsx
import { describe, expect, it, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { BrushingControls } from '@/components/selector/BrushingControls'
import { useSelectorStore } from '@/state/selectorSlice'
import { useBrushModeStore } from '@/components/selector/BrushingControls'

beforeEach(() => {
  useSelectorStore.getState().resetFilter()
  useSelectorStore.getState().clearBrush()
  useBrushModeStore.setState({ mode: 'replace' })
})

describe('BrushingControls', () => {
  it('switches the lasso mode when a button is clicked', () => {
    render(<BrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Add/ }))
    expect(useBrushModeStore.getState().mode).toBe('add')
  })

  it('undo button calls store.undoBrush', () => {
    useSelectorStore.getState().applyLasso([1, 2, 3], 'replace')
    render(<BrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Undo/ }))
    expect(useSelectorStore.getState().brushing.selectedIds.size).toBe(0)
  })

  it('clear button empties selection', () => {
    useSelectorStore.getState().applyLasso([1, 2, 3], 'replace')
    render(<BrushingControls />)
    fireEvent.click(screen.getByRole('button', { name: /Clear/ }))
    expect(useSelectorStore.getState().brushing.selectedIds.size).toBe(0)
  })
})
