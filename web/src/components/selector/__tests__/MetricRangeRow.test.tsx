// web/src/components/selector/__tests__/MetricRangeRow.test.tsx
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen, act } from '@testing-library/react'
import { MetricRangeRow } from '@/components/selector/MetricRangeRow'

beforeEach(() => {
  vi.useFakeTimers()
})
afterEach(() => {
  vi.useRealTimers()
})

describe('MetricRangeRow', () => {
  it('renders label + min/max inputs with default values', () => {
    const onChange = vi.fn()
    render(
      <MetricRangeRow
        metricKey="area"
        value={[0, 1_000_000]}
        onChange={onChange}
      />
    )
    expect(screen.getByText(/Area \(px\)/i)).not.toBeNull()
    const minInput = screen.getByLabelText('area min') as HTMLInputElement
    expect(minInput.value).toBe('0')
  })

  it('debounces commit by 200ms', () => {
    const onChange = vi.fn()
    render(
      <MetricRangeRow
        metricKey="std_r"
        value={[0, 100]}
        onChange={onChange}
      />
    )
    const max = screen.getByLabelText('std_r max') as HTMLInputElement
    fireEvent.change(max, { target: { value: '50' } })
    // before debounce fires
    expect(onChange).not.toHaveBeenCalled()
    act(() => { vi.advanceTimersByTime(200) })
    expect(onChange).toHaveBeenCalledWith([0, 50])
  })

  it('swaps min/max if user enters min > max', () => {
    const onChange = vi.fn()
    render(
      <MetricRangeRow
        metricKey="area"
        value={[0, 100]}
        onChange={onChange}
      />
    )
    const min = screen.getByLabelText('area min') as HTMLInputElement
    fireEvent.change(min, { target: { value: '500' } })
    act(() => { vi.advanceTimersByTime(200) })
    // ports tab_selector.py:181-184 — swap so committed range is [100, 500]
    expect(onChange).toHaveBeenCalledWith([100, 500])
  })

  it('does not fire onChange on mount or on prop-driven prop sync', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <MetricRangeRow metricKey="area" value={[0, 100]} onChange={onChange} />
    )
    // Advance past the debounce window without any user input.
    act(() => { vi.advanceTimersByTime(500) })
    expect(onChange).not.toHaveBeenCalled()

    // Parent drives a prop change (e.g. resetFilter) — must not feed back.
    rerender(
      <MetricRangeRow metricKey="area" value={[5, 50]} onChange={onChange} />
    )
    act(() => { vi.advanceTimersByTime(500) })
    expect(onChange).not.toHaveBeenCalled()
  })
})
