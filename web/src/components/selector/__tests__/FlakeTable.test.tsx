// web/src/components/selector/__tests__/FlakeTable.test.tsx
import { describe, expect, it, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { FlakeTable } from '@/components/selector/FlakeTable'
import { useSelectorStore } from '@/state/selectorSlice'

const stats = {
  flake_ids: [1, 2, 3],
  mean_r: [10, 20, 30],
  mean_g: [10, 20, 30],
  mean_b: [10, 20, 30],
  std_r: [1, 2, 3],
  std_g: [1, 2, 3],
  std_b: [1, 2, 3],
  areas: [100, 200, 300],
}

beforeEach(() => {
  useSelectorStore.getState().clearBrush()
})

describe('FlakeTable', () => {
  it('renders one row per accepted flake (default filter accepts all)', () => {
    render(<FlakeTable stats={stats} />)
    expect(screen.getAllByRole('row').length).toBe(4)
  })

  it('row click sets focusId on the store', () => {
    render(<FlakeTable stats={stats} />)
    fireEvent.click(screen.getByTestId('flake-row-2'))
    expect(useSelectorStore.getState().brushing.focusId).toBe(2)
  })
})
