import { useEffect, useReducer } from 'react'
import { flushSync } from 'react-dom'
import { useClusteringStore } from '@/state/clusteringSlice'

// Subscribes to the clustering store and forces a synchronous re-render via
// flushSync. Without this, store mutations performed outside of React event
// handlers (e.g. cross-tab broadcasts or direct API calls) would not commit to
// the DOM until the next React act() flush — leaving derived attributes like
// data-editing visibly stale.
function useSyncedClusteringStore(): void {
  const [, force] = useReducer((n: number) => n + 1, 0)
  useEffect(() => {
    return useClusteringStore.subscribe(() => {
      flushSync(() => force())
    })
  }, [])
}

export function SeedGroupList() {
  useSyncedClusteringStore()
  const { seedGroups: groups, editingGroupId, setEditingGroupId, removeSeedGroup } =
    useClusteringStore.getState()

  if (groups.length === 0) {
    return <div style={{ color: '#888', fontStyle: 'italic' }}>No seed groups yet. Lasso → "Add as seed group".</div>
  }

  return (
    <div role="list" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {groups.map((g) => {
        const editing = editingGroupId === g.id
        return (
          <div
            key={g.id}
            data-testid={`seed-group-row-${g.id}`}
            data-editing={editing ? 'true' : 'false'}
            role="listitem"
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 90px 60px 60px',
              alignItems: 'center',
              padding: '4px 8px',
              border: '1px solid #ddd',
              background: editing ? '#fef3c7' : 'transparent',
              borderRadius: 4,
            }}
          >
            <span>{g.name}</span>
            <span style={{ color: '#666', fontSize: 12 }}>{g.member_ids.length} members</span>
            <button
              type="button"
              data-testid={`seed-group-edit-${g.id}`}
              onClick={() => setEditingGroupId(editing ? null : g.id)}
            >
              {editing ? 'Done' : 'Edit'}
            </button>
            <button
              type="button"
              data-testid={`seed-group-delete-${g.id}`}
              onClick={() => removeSeedGroup(g.id)}
            >
              Delete
            </button>
          </div>
        )
      })}
    </div>
  )
}
