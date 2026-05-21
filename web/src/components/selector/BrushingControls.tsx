// web/src/components/selector/BrushingControls.tsx
/**
 * Lasso mode + undo/redo/clear buttons.
 *
 * Mode is held in a tiny local Zustand store rather than the SelectorSlice
 * because it's transient interaction state — the mode never affects
 * what we commit. ScatterCanvas reads the mode when it sends a lasso event.
 */
import { create } from 'zustand'
import type { LassoMode } from '@/lib/brushing'
import { useSelectorStore } from '@/state/selectorSlice'

interface BrushModeState {
  mode: LassoMode
  setMode(m: LassoMode): void
}

export const useBrushModeStore = create<BrushModeState>((set) => ({
  mode: 'replace',
  setMode(mode) {
    set({ mode })
  },
}))

const BUTTONS: Array<{ mode: LassoMode; label: string; title: string }> = [
  { mode: 'replace', label: 'Replace (R)', title: 'Replace selection with lassoed ids' },
  { mode: 'add', label: 'Add (A)', title: 'Union lassoed ids into selection' },
  { mode: 'remove', label: 'Remove (D)', title: 'Subtract lassoed ids from selection' },
]

export function BrushingControls() {
  const mode = useBrushModeStore((s) => s.mode)
  const setMode = useBrushModeStore((s) => s.setMode)
  const undo = useSelectorStore((s) => s.undoBrush)
  const redo = useSelectorStore((s) => s.redoBrush)
  const clear = useSelectorStore((s) => s.clearBrush)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, margin: '8px 0' }}>
      <div style={{ display: 'flex', gap: 4 }}>
        {BUTTONS.map((b) => (
          <button
            key={b.mode}
            title={b.title}
            onClick={() => setMode(b.mode)}
            aria-pressed={mode === b.mode}
            style={{
              fontWeight: mode === b.mode ? 700 : 400,
              border: mode === b.mode ? '2px solid #2563eb' : '1px solid #ccc',
            }}
          >
            {b.label}
          </button>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 4 }}>
        <button onClick={undo}>Undo</button>
        <button onClick={redo}>Redo</button>
        <button onClick={clear}>Clear</button>
      </div>
    </div>
  )
}
