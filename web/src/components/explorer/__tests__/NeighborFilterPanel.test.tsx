import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { NeighborFilterPanel } from '@/components/explorer/NeighborFilterPanel'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

beforeEach(() => { resetExplorerStore() })

describe('NeighborFilterPanel', () => {
  it('renders size_min, size_max, isolation_min inputs and a border-clipped checkbox', () => {
    render(<NeighborFilterPanel />)
    expect(screen.getByLabelText(/size min/i)).not.toBeNull()
    expect(screen.getByLabelText(/size max/i)).not.toBeNull()
    expect(screen.getByLabelText(/isolation min/i)).not.toBeNull()
    expect(screen.getByLabelText(/exclude border-clipped/i)).not.toBeNull()
  })

  it('typing into size_min writes through to neighborFilter.sizeMin', () => {
    render(<NeighborFilterPanel />)
    const inp = screen.getByLabelText(/size min/i) as HTMLInputElement
    fireEvent.change(inp, { target: { value: '3' } })
    expect(useExplorerStore.getState().neighborFilter.sizeMin).toBe(3)
  })

  it('typing into size_max writes through to neighborFilter.sizeMax', () => {
    render(<NeighborFilterPanel />)
    const inp = screen.getByLabelText(/size max/i) as HTMLInputElement
    fireEvent.change(inp, { target: { value: '20' } })
    expect(useExplorerStore.getState().neighborFilter.sizeMax).toBe(20)
  })

  it('clearing size_min sets sizeMin to null', () => {
    useExplorerStore.getState().setSizeRange(5, 10)
    render(<NeighborFilterPanel />)
    const inp = screen.getByLabelText(/size min/i) as HTMLInputElement
    fireEvent.change(inp, { target: { value: '' } })
    expect(useExplorerStore.getState().neighborFilter.sizeMin).toBeNull()
  })

  it('typing into isolation_min writes through to neighborFilter.isolationMin', () => {
    render(<NeighborFilterPanel />)
    const inp = screen.getByLabelText(/isolation min/i) as HTMLInputElement
    fireEvent.change(inp, { target: { value: '80' } })
    expect(useExplorerStore.getState().neighborFilter.isolationMin).toBe(80)
  })

  it('toggling exclude border-clipped flips the boolean', () => {
    render(<NeighborFilterPanel />)
    const cb = screen.getByLabelText(/exclude border-clipped/i) as HTMLInputElement
    fireEvent.click(cb)
    expect(useExplorerStore.getState().neighborFilter.excludeBorderClipped).toBe(true)
  })
})
