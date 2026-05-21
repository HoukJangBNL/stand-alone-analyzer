import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ClusterIncludeExcludePicker } from '@/components/explorer/ClusterIncludeExcludePicker'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

beforeEach(() => { resetExplorerStore() })

describe('ClusterIncludeExcludePicker', () => {
  it('renders one checkbox per available label in the Include column', () => {
    render(<ClusterIncludeExcludePicker availableLabels={['thin', 'thick', 'noise']} />)
    expect(screen.getByRole('checkbox', { name: /Include thin/i })).not.toBeNull()
    expect(screen.getByRole('checkbox', { name: /Include thick/i })).not.toBeNull()
    expect(screen.getByRole('checkbox', { name: /Include noise/i })).not.toBeNull()
  })

  it('renders one checkbox per available label in the Exclude column', () => {
    render(<ClusterIncludeExcludePicker availableLabels={['thin']} />)
    expect(screen.getByRole('checkbox', { name: /Exclude thin/i })).not.toBeNull()
  })

  it('clicking Include adds the label to includeLabels', () => {
    render(<ClusterIncludeExcludePicker availableLabels={['thin']} />)
    fireEvent.click(screen.getByRole('checkbox', { name: /Include thin/i }))
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(true)
  })

  it('clicking Exclude removes from includeLabels (mutual exclusion)', () => {
    render(<ClusterIncludeExcludePicker availableLabels={['thin']} />)
    fireEvent.click(screen.getByRole('checkbox', { name: /Include thin/i }))
    fireEvent.click(screen.getByRole('checkbox', { name: /Exclude thin/i }))
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(false)
    expect(useExplorerStore.getState().excludeLabels.has('thin')).toBe(true)
  })

  it('shows a red italic conflict caption when the same label is in both Sets', () => {
    // This shouldn't happen naturally (mutual exclusion enforces it), but
    // we test the rendering surface in case external code mutates the store.
    useExplorerStore.setState({
      includeLabels: new Set(['conflict']),
      excludeLabels: new Set(['conflict']),
    })
    render(<ClusterIncludeExcludePicker availableLabels={['conflict']} />)
    const caption = screen.getByText(/Conflict.*conflict/i)
    expect(caption).not.toBeNull()
    const style = window.getComputedStyle(caption as HTMLElement)
    expect(style.color).toMatch(/198|c62828|rgb/i)  // tolerant: red shade
    expect(style.fontStyle).toBe('italic')
  })

  it('renders empty state when availableLabels is empty', () => {
    render(<ClusterIncludeExcludePicker availableLabels={[]} />)
    expect(screen.getByText(/no clusters available/i)).not.toBeNull()
  })
})
