import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { FitScopeRadio } from '@/components/clustering/FitScopeRadio'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('FitScopeRadio', () => {
  it('default selection is "seeds"', () => {
    render(<FitScopeRadio />)
    expect((screen.getByLabelText(/seeds only/i) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText(/all selected/i) as HTMLInputElement).checked).toBe(false)
  })

  it('clicking "all selected" updates the store', () => {
    render(<FitScopeRadio />)
    fireEvent.click(screen.getByLabelText(/all selected/i))
    expect(useClusteringStore.getState().fitScope).toBe('all_selected')
  })
})
