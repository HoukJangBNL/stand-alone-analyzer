import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { InitialMahalanobisSlider } from '@/components/clustering/InitialMahalanobisSlider'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('InitialMahalanobisSlider', () => {
  it('renders the default value 3.0', () => {
    render(<InitialMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(input.value)).toBe(3.0)
  })

  it('change event writes initialMaxMahalanobis', () => {
    render(<InitialMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    fireEvent.change(input, { target: { value: '4.5' } })
    expect(useClusteringStore.getState().initialMaxMahalanobis).toBe(4.5)
  })

  it('respects 0.5–6.0 bounds (per design §3.4)', () => {
    render(<InitialMahalanobisSlider />)
    const input = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(input.min)).toBe(0.5)
    expect(parseFloat(input.max)).toBe(6.0)
  })
})
