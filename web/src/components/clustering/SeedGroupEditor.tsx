import { useState } from 'react'
import { useClusteringStore } from '@/state/clusteringSlice'
import { SeedGroupList } from './SeedGroupList'

export function SeedGroupEditor() {
  const [name, setName] = useState('')
  const selectedIds = useClusteringStore((s) => s.brushing.selectedIds)
  const addSeedGroup = useClusteringStore((s) => s.addSeedGroup)
  const clearSeedGroups = useClusteringStore((s) => s.clearSeedGroups)

  const canAdd = selectedIds.size > 0

  function handleAdd() {
    if (!canAdd) return
    const memberIds = Array.from(selectedIds)
    const finalName = name.trim() || `group ${Date.now() % 10000}`
    addSeedGroup(finalName, memberIds)
    setName('')
  }

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <h4 style={{ margin: 0 }}>Seed groups</h4>
      <SeedGroupList />
      <div style={{ display: 'flex', gap: 4 }}>
        <input
          data-testid="clustering-seed-name"
          type="text"
          placeholder="seed group name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ flex: 1, padding: '4px 6px' }}
        />
        <button data-testid="clustering-seed-add" type="button" onClick={handleAdd} disabled={!canAdd}>
          Add from selection ({selectedIds.size})
        </button>
      </div>
      <div>
        <button data-testid="clustering-seed-clear" type="button" onClick={clearSeedGroups}>
          Clear all
        </button>
      </div>
    </section>
  )
}
