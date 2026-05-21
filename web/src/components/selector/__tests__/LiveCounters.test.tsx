// web/src/components/selector/__tests__/LiveCounters.test.tsx
import { describe, expect, it, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { LiveCounters } from '@/components/selector/LiveCounters'
import { useSelectorStore } from '@/state/selectorSlice'

beforeEach(() => {
  useSelectorStore.getState().resetFilter()
  useSelectorStore.getState().clearBrush()
})

describe('LiveCounters', () => {
  it('renders 4 counters from given stats + selection', () => {
    const stats = {
      flake_ids: [1, 2, 3, 4, 5],
      mean_r: [10, 20, 30, 40, 50],
      mean_g: [10, 20, 30, 40, 50],
      mean_b: [10, 20, 30, 40, 50],
      std_r: [5, 5, 5, 5, 5],
      std_g: [5, 5, 5, 5, 5],
      std_b: [5, 5, 5, 5, 5],
      areas: [10, 20, 30, 40, 50],
    }
    useSelectorStore.getState().setFilter('area', [15, 45])
    useSelectorStore.getState().applyLasso([2, 3], 'replace')

    render(<LiveCounters stats={stats} />)
    expect(screen.getByTestId('counter-accepted').textContent).toContain('3')
    expect(screen.getByTestId('counter-rejected').textContent).toContain('2')
    expect(screen.getByTestId('counter-selected').textContent).toContain('2')
    expect(screen.getByTestId('counter-will-commit').textContent).toContain('2')
  })

  it('will-commit equals accepted when lasso is empty (filter-only commit)', () => {
    const stats = {
      flake_ids: [1, 2, 3],
      mean_r: [0, 0, 0], mean_g: [0, 0, 0], mean_b: [0, 0, 0],
      std_r: [0, 0, 0], std_g: [0, 0, 0], std_b: [0, 0, 0],
      areas: [10, 20, 30],
    }
    useSelectorStore.getState().setFilter('area', [15, 25])
    render(<LiveCounters stats={stats} />)
    expect(screen.getByTestId('counter-accepted').textContent).toContain('1')
    expect(screen.getByTestId('counter-will-commit').textContent).toContain('1')
  })
})
