import { describe, it, expect, beforeEach } from 'vitest'
import {
  useExplorerStore,
  resetExplorerStore,
} from '@/state/explorerSlice'

beforeEach(() => {
  resetExplorerStore()
})

describe('explorerSlice — initial state', () => {
  it('starts with empty include/exclude sets and null selections', () => {
    const s = useExplorerStore.getState()
    expect(s.includeLabels.size).toBe(0)
    expect(s.excludeLabels.size).toBe(0)
    expect(s.selectedFlakeId).toBeNull()
    expect(s.focusFlakeId).toBeNull()
    expect(s.lodChoice).toBe('auto')
    expect(s.viewportState).toBeNull()
  })

  it('defaults render toggles to (true, false, false, true) per Plan v34', () => {
    const t = useExplorerStore.getState().renderToggles
    expect(t.flake_bbox).toBe(true)
    expect(t.flake_outline).toBe(false)
    expect(t.island_bbox).toBe(false)
    expect(t.island_outline).toBe(true)
  })

  it('defaults neighborFilter to all-null + exclude_border_clipped=false', () => {
    const nf = useExplorerStore.getState().neighborFilter
    expect(nf.sizeMin).toBeNull()
    expect(nf.sizeMax).toBeNull()
    expect(nf.isolationMin).toBeNull()
    expect(nf.excludeBorderClipped).toBe(false)
  })
})

describe('explorerSlice — Include/Exclude actions', () => {
  it('addInclude inserts the label as a new Set entry', () => {
    useExplorerStore.getState().addInclude('thin')
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(true)
  })

  it('addInclude removes the same label from excludeLabels (mutual exclusion)', () => {
    useExplorerStore.getState().addExclude('noise')
    useExplorerStore.getState().addInclude('noise')
    expect(useExplorerStore.getState().includeLabels.has('noise')).toBe(true)
    expect(useExplorerStore.getState().excludeLabels.has('noise')).toBe(false)
  })

  it('removeInclude removes the label only from includeLabels', () => {
    useExplorerStore.getState().addInclude('thin')
    useExplorerStore.getState().removeInclude('thin')
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(false)
  })

  it('addExclude removes the same label from includeLabels (mutual exclusion)', () => {
    useExplorerStore.getState().addInclude('thin')
    useExplorerStore.getState().addExclude('thin')
    expect(useExplorerStore.getState().excludeLabels.has('thin')).toBe(true)
    expect(useExplorerStore.getState().includeLabels.has('thin')).toBe(false)
  })

  it('clearLabels resets both Sets', () => {
    useExplorerStore.getState().addInclude('a')
    useExplorerStore.getState().addExclude('b')
    useExplorerStore.getState().clearLabels()
    expect(useExplorerStore.getState().includeLabels.size).toBe(0)
    expect(useExplorerStore.getState().excludeLabels.size).toBe(0)
  })
})

describe('explorerSlice — neighborFilter actions', () => {
  it('setSizeRange writes both bounds atomically', () => {
    useExplorerStore.getState().setSizeRange(2, 10)
    expect(useExplorerStore.getState().neighborFilter.sizeMin).toBe(2)
    expect(useExplorerStore.getState().neighborFilter.sizeMax).toBe(10)
  })

  it('setSizeRange(null, null) clears both bounds', () => {
    useExplorerStore.getState().setSizeRange(2, 10)
    useExplorerStore.getState().setSizeRange(null, null)
    expect(useExplorerStore.getState().neighborFilter.sizeMin).toBeNull()
    expect(useExplorerStore.getState().neighborFilter.sizeMax).toBeNull()
  })

  it('setIsolationMin writes the isolation threshold', () => {
    useExplorerStore.getState().setIsolationMin(80)
    expect(useExplorerStore.getState().neighborFilter.isolationMin).toBe(80)
  })

  it('setExcludeBorderClipped flips the boolean', () => {
    useExplorerStore.getState().setExcludeBorderClipped(true)
    expect(useExplorerStore.getState().neighborFilter.excludeBorderClipped).toBe(true)
  })
})

describe('explorerSlice — selection + viewport + LOD + toggles', () => {
  it('setSelectedFlakeId / setFocusFlakeId update independently', () => {
    useExplorerStore.getState().setSelectedFlakeId(42)
    useExplorerStore.getState().setFocusFlakeId(7)
    expect(useExplorerStore.getState().selectedFlakeId).toBe(42)
    expect(useExplorerStore.getState().focusFlakeId).toBe(7)
  })

  it('setLodChoice accepts auto and 0..3', () => {
    useExplorerStore.getState().setLodChoice(2)
    expect(useExplorerStore.getState().lodChoice).toBe(2)
    useExplorerStore.getState().setLodChoice('auto')
    expect(useExplorerStore.getState().lodChoice).toBe('auto')
  })

  it('setViewportState stores and clears', () => {
    useExplorerStore.getState().setViewportState({ center: [0.5, 0.5], zoom: 1.0 })
    expect(useExplorerStore.getState().viewportState).toEqual({
      center: [0.5, 0.5], zoom: 1.0,
    })
    useExplorerStore.getState().setViewportState(null)
    expect(useExplorerStore.getState().viewportState).toBeNull()
  })

  it('toggleRender flips a single toggle key', () => {
    useExplorerStore.getState().toggleRender('flake_outline')
    expect(useExplorerStore.getState().renderToggles.flake_outline).toBe(true)
    useExplorerStore.getState().toggleRender('flake_outline')
    expect(useExplorerStore.getState().renderToggles.flake_outline).toBe(false)
  })

  it('resetExplorerStore returns every field to default', () => {
    useExplorerStore.getState().addInclude('a')
    useExplorerStore.getState().setSelectedFlakeId(1)
    useExplorerStore.getState().setLodChoice(3)
    useExplorerStore.getState().setSizeRange(1, 99)
    resetExplorerStore()
    const s = useExplorerStore.getState()
    expect(s.includeLabels.size).toBe(0)
    expect(s.selectedFlakeId).toBeNull()
    expect(s.lodChoice).toBe('auto')
    expect(s.neighborFilter.sizeMin).toBeNull()
    expect(s.renderToggles.flake_bbox).toBe(true)
    expect(s.renderToggles.island_outline).toBe(true)
  })
})
