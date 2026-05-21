import { useExplorerStore } from '@/state/explorerSlice'

export function NeighborFilterPanel() {
  const nf = useExplorerStore((s) => s.neighborFilter)
  const setSizeRange = useExplorerStore((s) => s.setSizeRange)
  const setIsolationMin = useExplorerStore((s) => s.setIsolationMin)
  const setExcludeBorderClipped = useExplorerStore((s) => s.setExcludeBorderClipped)

  function parseOrNull(v: string): number | null {
    if (v === '') return null
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }

  return (
    <fieldset aria-label="neighbor filter">
      <legend>Neighbor filter</legend>
      <label>
        Size min
        <input
          type="number"
          min={1}
          value={nf.sizeMin ?? ''}
          onChange={(e) => setSizeRange(parseOrNull(e.target.value), nf.sizeMax)}
          aria-label="size min"
        />
      </label>
      <label>
        Size max
        <input
          type="number"
          min={1}
          value={nf.sizeMax ?? ''}
          onChange={(e) => setSizeRange(nf.sizeMin, parseOrNull(e.target.value))}
          aria-label="size max"
        />
      </label>
      <label>
        Isolation min (px)
        <input
          type="number"
          min={0}
          value={nf.isolationMin ?? ''}
          onChange={(e) => setIsolationMin(parseOrNull(e.target.value))}
          aria-label="isolation min"
        />
      </label>
      <label>
        <input
          type="checkbox"
          checked={nf.excludeBorderClipped}
          onChange={(e) => setExcludeBorderClipped(e.target.checked)}
          aria-label="exclude border-clipped"
        />
        Exclude border-clipped flakes
      </label>
    </fieldset>
  )
}
