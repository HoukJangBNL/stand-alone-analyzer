import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { RenderTogglesPanel } from '@/components/explorer/RenderTogglesPanel'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

beforeEach(() => { resetExplorerStore() })

describe('RenderTogglesPanel — Plan v34 defaults + state-only no-ops (pinned #10)', () => {
  it('renders 4 checkboxes for the 4 toggle keys', () => {
    render(<RenderTogglesPanel />)
    expect(screen.getByLabelText(/flake bbox/i)).not.toBeNull()
    expect(screen.getByLabelText(/flake outline/i)).not.toBeNull()
    expect(screen.getByLabelText(/island bbox/i)).not.toBeNull()
    expect(screen.getByLabelText(/island outline/i)).not.toBeNull()
  })

  it('flake bbox starts checked (true) per Plan v34 default', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/flake bbox/i) as HTMLInputElement
    expect(cb.checked).toBe(true)
  })

  it('island outline starts checked (true) per Plan v34 default', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/island outline/i) as HTMLInputElement
    expect(cb.checked).toBe(true)
  })

  it('flake outline starts unchecked (false) per Plan v34 default', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/flake outline/i) as HTMLInputElement
    expect(cb.checked).toBe(false)
  })

  it('island bbox starts unchecked (false) per Plan v34 default', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/island bbox/i) as HTMLInputElement
    expect(cb.checked).toBe(false)
  })

  it('toggling flake_outline writes through to renderToggles.flake_outline', () => {
    render(<RenderTogglesPanel />)
    const cb = screen.getByLabelText(/flake outline/i) as HTMLInputElement
    fireEvent.click(cb)
    expect(useExplorerStore.getState().renderToggles.flake_outline).toBe(true)
  })
})
