import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { RegCovarSlider } from '@/components/clustering/RegCovarSlider'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

describe('<RegCovarSlider>', () => {
  beforeEach(() => resetClusteringStore())

  it('renders with the slice default (10.0)', () => {
    render(<RegCovarSlider />)
    const slider = screen.getByTestId('clustering-reg-covar-slider') as HTMLInputElement
    expect(slider).toBeTruthy()
    // Initial slider t=1 (max) since default is 10.0.
    expect(parseFloat(slider.value)).toBeCloseTo(1, 4)
  })

  it('updates the slice when dragged to mid-band (~1.0)', () => {
    render(<RegCovarSlider />)
    const slider = screen.getByTestId('clustering-reg-covar-slider')
    // t=0.5 in log[0.1, 10.0] = sqrt(0.1*10) = 1.0
    fireEvent.change(slider, { target: { value: '0.5' } })
    expect(useClusteringStore.getState().regCovar).toBeCloseTo(1.0, 4)
  })

  it('shows the current numeric value next to the slider', () => {
    render(<RegCovarSlider />)
    const valueLabel = screen.getByTestId('clustering-reg-covar-value')
    expect(valueLabel.textContent).toMatch(/10\.00/)
  })
})
