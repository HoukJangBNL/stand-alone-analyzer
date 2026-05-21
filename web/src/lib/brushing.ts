// web/src/lib/brushing.ts
export type LassoMode = 'replace' | 'add' | 'remove'

export interface BrushingState {
  selectedIds: Set<number>
  focusId: number | null
  history: Array<Set<number>>
  redoStack: Array<Set<number>>
}

export function emptyBrushing(): BrushingState {
  return {
    selectedIds: new Set(),
    focusId: null,
    history: [],
    redoStack: [],
  }
}

function pushHistory(s: BrushingState, prior: Set<number>): BrushingState {
  return {
    ...s,
    history: [...s.history, prior],
    redoStack: [],
  }
}

export function applyLasso(
  s: BrushingState,
  ids: number[],
  mode: LassoMode
): BrushingState {
  const prior = new Set(s.selectedIds)
  let next: Set<number>
  if (mode === 'replace') {
    next = new Set(ids)
  } else if (mode === 'add') {
    next = new Set(prior)
    for (const id of ids) next.add(id)
  } else {
    next = new Set(prior)
    for (const id of ids) next.delete(id)
  }
  const withHist = pushHistory(s, prior)
  return { ...withHist, selectedIds: next }
}

export function undo(s: BrushingState): BrushingState {
  if (s.history.length === 0) return s
  const prior = s.history[s.history.length - 1]
  const newHistory = s.history.slice(0, -1)
  const current = new Set(s.selectedIds)
  return {
    ...s,
    selectedIds: prior,
    history: newHistory,
    redoStack: [...s.redoStack, current],
  }
}

export function redo(s: BrushingState): BrushingState {
  if (s.redoStack.length === 0) return s
  const next = s.redoStack[s.redoStack.length - 1]
  const newRedo = s.redoStack.slice(0, -1)
  const current = new Set(s.selectedIds)
  return {
    ...s,
    selectedIds: next,
    history: [...s.history, current],
    redoStack: newRedo,
  }
}

export function clearBrush(s: BrushingState): BrushingState {
  const prior = new Set(s.selectedIds)
  const withHist = pushHistory(s, prior)
  return { ...withHist, selectedIds: new Set() }
}

export function setFocusId(s: BrushingState, focusId: number | null): BrushingState {
  return { ...s, focusId }
}
