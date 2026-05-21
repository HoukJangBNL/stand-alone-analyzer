// web/src/components/explorer/NeighborFilterPanel.tsx
// W3.3: NeighborFilterPanel survives the Explorer dead-control purge. The four
// controls are kept verbatim but their downstream semantics are evolving:
// - sizeMin/sizeMax: server-filtered today via /explorer/flakes?size_min=...
// - isolationMin: written to slice and POSTed in save_state body, but NOT yet
//   used by /explorer/flakes; will map to flake_analyses.curation_params.
// - excludeBorderClipped: same situation as isolationMin.
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
    <fieldset aria-label="neighbor filter" data-testid="explorer-neighbor-filter">
      <legend>Neighbor filter</legend>
      <label>
        Size min
        <input
          data-testid="explorer-neighbor-size-min"
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
          data-testid="explorer-neighbor-size-max"
          type="number"
          min={1}
          value={nf.sizeMax ?? ''}
          onChange={(e) => setSizeRange(nf.sizeMin, parseOrNull(e.target.value))}
          aria-label="size max"
        />
      </label>
      {/* TODO(flake_analyses): map to curation_params.neighbor_isolation_min */}
      <label>
        Isolation min (px)
        <input
          data-testid="explorer-neighbor-isolation-min"
          type="number"
          min={0}
          value={nf.isolationMin ?? ''}
          onChange={(e) => setIsolationMin(parseOrNull(e.target.value))}
          aria-label="isolation min"
        />
      </label>
      {/* TODO(flake_analyses): map to curation_params.exclude_border_clipped */}
      <label>
        <input
          data-testid="explorer-neighbor-exclude-border"
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
