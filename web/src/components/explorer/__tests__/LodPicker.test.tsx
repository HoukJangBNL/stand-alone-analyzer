import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LodPicker } from '@/components/explorer/LodPicker'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

beforeEach(() => { resetExplorerStore() })

describe('LodPicker', () => {
  it('renders one radio per choice (auto, lod0, lod1, lod2, raw)', () => {
    render(<LodPicker />)
    expect(screen.getByRole('radio', { name: /auto/i })).not.toBeNull()
    expect(screen.getByRole('radio', { name: /lod0/i })).not.toBeNull()
    expect(screen.getByRole('radio', { name: /lod1/i })).not.toBeNull()
    expect(screen.getByRole('radio', { name: /lod2/i })).not.toBeNull()
    expect(screen.getByRole('radio', { name: /raw/i })).not.toBeNull()
  })

  it('starts with auto selected', () => {
    render(<LodPicker />)
    const auto = screen.getByRole('radio', { name: /auto/i }) as HTMLInputElement
    expect(auto.checked).toBe(true)
  })

  it('clicking lod1 writes 1 to lodChoice', () => {
    render(<LodPicker />)
    fireEvent.click(screen.getByRole('radio', { name: /lod1/i }))
    expect(useExplorerStore.getState().lodChoice).toBe(1)
  })

  it('clicking raw writes 3 to lodChoice', () => {
    render(<LodPicker />)
    fireEvent.click(screen.getByRole('radio', { name: /raw/i }))
    expect(useExplorerStore.getState().lodChoice).toBe(3)
  })

  it('clicking auto reverts lodChoice to "auto"', () => {
    useExplorerStore.getState().setLodChoice(2)
    render(<LodPicker />)
    fireEvent.click(screen.getByRole('radio', { name: /auto/i }))
    expect(useExplorerStore.getState().lodChoice).toBe('auto')
  })
})
