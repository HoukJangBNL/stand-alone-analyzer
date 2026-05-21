import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SeedGroupEditor } from '@/components/clustering/SeedGroupEditor'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
})

describe('SeedGroupEditor', () => {
  it('"Add from selection" is disabled when brushing.selectedIds is empty', () => {
    render(<SeedGroupEditor />)
    const btn = screen.getByRole('button', { name: /Add from selection/ })
    expect((btn as HTMLButtonElement).disabled).toBe(true)
  })

  it('"Add from selection" appends a seed group from current brush', () => {
    useClusteringStore.getState().applyLasso([10, 11, 12], 'replace')
    render(<SeedGroupEditor />)
    const nameInput = screen.getByPlaceholderText(/seed group name/i) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'monolayer' } })
    fireEvent.click(screen.getByRole('button', { name: /Add from selection/ }))
    const groups = useClusteringStore.getState().seedGroups
    expect(groups.length).toBe(1)
    expect(groups[0].name).toBe('monolayer')
    expect(groups[0].member_ids).toEqual([10, 11, 12])
  })

  it('after add, the name input is cleared', () => {
    useClusteringStore.getState().applyLasso([1], 'replace')
    render(<SeedGroupEditor />)
    const nameInput = screen.getByPlaceholderText(/seed group name/i) as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: /Add from selection/ }))
    expect(nameInput.value).toBe('')
  })

  it('"Clear all" wipes seed groups when confirmed', () => {
    useClusteringStore.getState().addSeedGroup('a', [1])
    useClusteringStore.getState().addSeedGroup('b', [2])
    render(<SeedGroupEditor />)
    fireEvent.click(screen.getByRole('button', { name: /Clear all/ }))
    expect(useClusteringStore.getState().seedGroups).toEqual([])
  })
})
