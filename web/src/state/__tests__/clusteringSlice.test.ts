import { describe, it, expect, beforeEach } from 'vitest'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

describe('clusteringSlice', () => {
  beforeEach(() => {
    resetClusteringStore()
  })

  it('default state matches design §3.4', () => {
    const s = useClusteringStore.getState()
    expect(s.seedGroups).toEqual([])
    expect(s.fitScope).toBe('seeds')
    expect(s.initialMaxMahalanobis).toBe(3.0)
    expect(s.liveMaxMahalanobis).toBe(3.0)
    expect(s.perClusterThresholds).toEqual({})
    expect(s.editingGroupId).toBeNull()
    expect(s.brushing.selectedIds.size).toBe(0)
  })

  it("addSeedGroup appends a uniquely-id'd group", () => {
    const { addSeedGroup } = useClusteringStore.getState()
    addSeedGroup('thin', [1, 2, 3])
    const groups = useClusteringStore.getState().seedGroups
    expect(groups.length).toBe(1)
    expect(groups[0].name).toBe('thin')
    expect(groups[0].member_ids).toEqual([1, 2, 3])
    expect(typeof groups[0].id).toBe('string')
  })

  it('renameSeedGroup updates only the matching group', () => {
    const { addSeedGroup, renameSeedGroup } = useClusteringStore.getState()
    addSeedGroup('thin', [1])
    addSeedGroup('thick', [2])
    const id = useClusteringStore.getState().seedGroups[0].id
    renameSeedGroup(id, 'super-thin')
    const groups = useClusteringStore.getState().seedGroups
    expect(groups[0].name).toBe('super-thin')
    expect(groups[1].name).toBe('thick')
  })

  it('removeSeedGroup drops the matching group', () => {
    const { addSeedGroup, removeSeedGroup } = useClusteringStore.getState()
    addSeedGroup('thin', [1])
    addSeedGroup('thick', [2])
    const id = useClusteringStore.getState().seedGroups[0].id
    removeSeedGroup(id)
    const groups = useClusteringStore.getState().seedGroups
    expect(groups.length).toBe(1)
    expect(groups[0].name).toBe('thick')
  })

  it('clearSeedGroups removes every group', () => {
    const { addSeedGroup, clearSeedGroups } = useClusteringStore.getState()
    addSeedGroup('a', [1])
    addSeedGroup('b', [2])
    clearSeedGroups()
    expect(useClusteringStore.getState().seedGroups).toEqual([])
  })

  it('setThreshold writes per-cluster threshold', () => {
    const { setThreshold } = useClusteringStore.getState()
    setThreshold(0, 0.7)
    setThreshold(1, 0.3)
    const t = useClusteringStore.getState().perClusterThresholds
    expect(t[0]).toBe(0.7)
    expect(t[1]).toBe(0.3)
  })

  it('resetThresholdsToDefault clears overrides', () => {
    const { setThreshold, resetThresholdsToDefault } = useClusteringStore.getState()
    setThreshold(0, 0.9)
    resetThresholdsToDefault()
    expect(useClusteringStore.getState().perClusterThresholds).toEqual({})
  })

  it('setEditingGroupId toggles edit highlight target', () => {
    const { setEditingGroupId } = useClusteringStore.getState()
    setEditingGroupId('g-1')
    expect(useClusteringStore.getState().editingGroupId).toBe('g-1')
    setEditingGroupId(null)
    expect(useClusteringStore.getState().editingGroupId).toBeNull()
  })

  it('applyLasso updates brushing.selectedIds', () => {
    const { applyLasso } = useClusteringStore.getState()
    applyLasso([1, 2, 3], 'replace')
    expect(useClusteringStore.getState().brushing.selectedIds).toEqual(new Set([1, 2, 3]))
  })

  it('setLiveMaxMahalanobis writes liveMaxMahalanobis', () => {
    const { setLiveMaxMahalanobis } = useClusteringStore.getState()
    setLiveMaxMahalanobis(2.5)
    expect(useClusteringStore.getState().liveMaxMahalanobis).toBe(2.5)
  })

  it('setInitialMaxMahalanobis writes initialMaxMahalanobis', () => {
    const { setInitialMaxMahalanobis } = useClusteringStore.getState()
    setInitialMaxMahalanobis(4.5)
    expect(useClusteringStore.getState().initialMaxMahalanobis).toBe(4.5)
  })

  it('setFitScope flips between seeds and all_selected', () => {
    const { setFitScope } = useClusteringStore.getState()
    setFitScope('all_selected')
    expect(useClusteringStore.getState().fitScope).toBe('all_selected')
  })

  it('hydrateSeedGroups installs disk groups when state is empty', () => {
    const { hydrateSeedGroups } = useClusteringStore.getState()
    hydrateSeedGroups([
      { name: 'thin', domain_ids: [1, 2] },
      { name: 'thick', domain_ids: [3] },
    ])
    const sg = useClusteringStore.getState().seedGroups
    expect(sg.map((g) => g.name)).toEqual(['thin', 'thick'])
    expect(sg[0].member_ids).toEqual([1, 2])
  })

  it('hydrateSeedGroups does NOT clobber in-flight edits (preserves _maybe_autoload_seed_groups semantics)', () => {
    const { addSeedGroup, hydrateSeedGroups } = useClusteringStore.getState()
    addSeedGroup('user-edit', [99])
    hydrateSeedGroups([{ name: 'disk', domain_ids: [1] }])
    const sg = useClusteringStore.getState().seedGroups
    expect(sg.length).toBe(1)
    expect(sg[0].name).toBe('user-edit')
  })
})
