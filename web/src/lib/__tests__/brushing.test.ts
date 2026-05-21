import { describe, expect, it } from 'vitest'
import {
  emptyBrushing,
  applyLasso,
  type BrushingState,
  undo,
  redo,
  clearBrush,
  setFocusId,
} from '@/lib/brushing'

const ids = (s: Set<number>) => Array.from(s).sort((a, b) => a - b)

describe('applyLasso', () => {
  it('replace mode replaces selection', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2, 3], 'replace')
    expect(ids(s.selectedIds)).toEqual([1, 2, 3])
    s = applyLasso(s, [4, 5], 'replace')
    expect(ids(s.selectedIds)).toEqual([4, 5])
  })

  it('add mode unions ids', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [2, 3], 'add')
    expect(ids(s.selectedIds)).toEqual([1, 2, 3])
  })

  it('remove mode subtracts ids', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2, 3, 4], 'replace')
    s = applyLasso(s, [2, 3], 'remove')
    expect(ids(s.selectedIds)).toEqual([1, 4])
  })

  it('pushes onto history for undo', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [3], 'add')
    expect(s.history.length).toBe(2)
  })
})

describe('undo / redo', () => {
  it('undo reverts to previous state', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [3], 'add')
    s = undo(s)
    expect(ids(s.selectedIds)).toEqual([1, 2])
  })

  it('redo replays undone state', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [3], 'add')
    s = undo(s)
    s = redo(s)
    expect(ids(s.selectedIds)).toEqual([1, 2, 3])
  })

  it('redo stack cleared when a new lasso is applied after undo', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = applyLasso(s, [3], 'add')
    s = undo(s)
    s = applyLasso(s, [9], 'add')
    s = redo(s)
    // redo is a no-op now
    expect(ids(s.selectedIds)).toEqual([1, 2, 9])
  })
})

describe('clearBrush', () => {
  it('clears selection but preserves focus', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = setFocusId(s, 7)
    s = clearBrush(s)
    expect(s.selectedIds.size).toBe(0)
    expect(s.focusId).toBe(7)
  })
})

describe('setFocusId', () => {
  it('sets focus without touching selection', () => {
    let s: BrushingState = emptyBrushing()
    s = applyLasso(s, [1, 2], 'replace')
    s = setFocusId(s, 5)
    expect(s.focusId).toBe(5)
    expect(ids(s.selectedIds)).toEqual([1, 2])
  })
})
