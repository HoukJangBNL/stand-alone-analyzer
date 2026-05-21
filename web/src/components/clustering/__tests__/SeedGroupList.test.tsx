import { describe, it, expect, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SeedGroupList } from '@/components/clustering/SeedGroupList'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

beforeEach(() => {
  resetClusteringStore()
  useClusteringStore.getState().addSeedGroup('thin', [1, 2, 3])
  useClusteringStore.getState().addSeedGroup('thick', [4, 5])
})

describe('SeedGroupList', () => {
  it('renders one row per seed group with name and member count', () => {
    render(<SeedGroupList />)
    expect(screen.getByText('thin')).not.toBeNull()
    expect(screen.getByText('thick')).not.toBeNull()
    expect(screen.getByText('3 members')).not.toBeNull()
    expect(screen.getByText('2 members')).not.toBeNull()
  })

  it('clicking edit toggles editingGroupId', () => {
    render(<SeedGroupList />)
    const id = useClusteringStore.getState().seedGroups[0].id
    fireEvent.click(screen.getByTestId(`seed-group-edit-${id}`))
    expect(useClusteringStore.getState().editingGroupId).toBe(id)
  })

  it('clicking delete removes the group from the store', () => {
    render(<SeedGroupList />)
    const id = useClusteringStore.getState().seedGroups[0].id
    fireEvent.click(screen.getByTestId(`seed-group-delete-${id}`))
    expect(useClusteringStore.getState().seedGroups.length).toBe(1)
    expect(useClusteringStore.getState().seedGroups[0].name).toBe('thick')
  })

  it('row in edit mode renders with data-editing="true"', () => {
    render(<SeedGroupList />)
    const id = useClusteringStore.getState().seedGroups[0].id
    useClusteringStore.getState().setEditingGroupId(id)
    const row = screen.getByTestId(`seed-group-row-${id}`)
    expect(row.getAttribute('data-editing')).toBe('true')
  })
})
