import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { LiveMahalanobisSlider } from '@/components/clustering/LiveMahalanobisSlider'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('LiveMahalanobisSlider', () => {
  it('default value is 3.0', () => {
    render(<LiveMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(input.value)).toBe(3.0)
  })

  it('change updates liveMaxMahalanobis', () => {
    render(<LiveMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    fireEvent.change(input, { target: { value: '5.5' } })
    expect(useClusteringStore.getState().liveMaxMahalanobis).toBe(5.5)
  })

  it('respects 0.5–8.0 bounds (per design §3.4 post-fit gate)', () => {
    render(<LiveMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(input.min)).toBe(0.5)
    expect(parseFloat(input.max)).toBe(8.0)
  })
})
