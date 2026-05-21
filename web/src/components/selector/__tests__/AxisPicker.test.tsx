// web/src/components/selector/__tests__/AxisPicker.test.tsx
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { AxisPicker } from '@/components/selector/AxisPicker'
import { useSelectorStore } from '@/state/selectorSlice'

describe('AxisPicker', () => {
  it('renders 8 radios (R, G, B, area, std_r, std_g, std_b, sam2)', () => {
    render(<AxisPicker pane="X" />)
    const radios = screen.getAllByRole('radio')
    expect(radios.length).toBe(8)
  })

  it('updates store when an axis is picked', () => {
    render(<AxisPicker pane="X" />)
    const areaRadio = screen.getByLabelText('X: area') as HTMLInputElement
    fireEvent.click(areaRadio)
    expect(useSelectorStore.getState().axisX).toBe('area')
  })

  it('Y pane writes to axisY', () => {
    render(<AxisPicker pane="Y" />)
    const sam2 = screen.getByLabelText('Y: sam2') as HTMLInputElement
    fireEvent.click(sam2)
    expect(useSelectorStore.getState().axisY).toBe('sam2')
  })
})
