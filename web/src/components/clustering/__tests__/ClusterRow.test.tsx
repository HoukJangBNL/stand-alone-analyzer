import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ClusterRow } from '@/components/clustering/ClusterRow'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
  vi.useFakeTimers()
})

describe('ClusterRow', () => {
  it('renders color swatch and "K/N pass" stats', () => {
    render(<ClusterRow clusterId={0} clusterName="thin" passCount={42} totalCount={100} />)
    expect(screen.getByText(/42 \/ 100/).textContent).toContain('42 / 100')
    expect(screen.getByTestId('cluster-swatch-0')).not.toBeNull()
  })

  it('slider change writes setThreshold (debounced 100ms)', () => {
    render(<ClusterRow clusterId={0} clusterName="thin" passCount={0} totalCount={1} />)
    const slider = screen.getByRole('slider') as HTMLInputElement
    fireEvent.change(slider, { target: { value: '0.7' } })
    // Pre-debounce: store still has default
    expect(useClusteringStore.getState().perClusterThresholds[0]).toBeUndefined()
    vi.advanceTimersByTime(100)
    expect(useClusteringStore.getState().perClusterThresholds[0]).toBe(0.7)
  })

  it('reads default threshold 0.5 when no override is set', () => {
    render(<ClusterRow clusterId={2} clusterName="x" passCount={0} totalCount={0} />)
    const slider = screen.getByRole('slider') as HTMLInputElement
    expect(parseFloat(slider.value)).toBe(0.5)
  })
})
