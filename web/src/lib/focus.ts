// web/src/lib/focus.ts
import type { BrushingState } from '@/lib/brushing'

export function pickFocusDomainId(s: BrushingState): number | null {
  if (s.focusId !== null && s.focusId !== undefined) {
    return s.focusId
  }
  if (s.selectedIds.size === 0) {
    return null
  }
  let min = Infinity
  for (const id of s.selectedIds) if (id < min) min = id
  return min === Infinity ? null : min
}
