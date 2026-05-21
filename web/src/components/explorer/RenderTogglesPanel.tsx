// Pinned decision #10: state-only no-ops. Mosaic does NOT consume these in Plan 4.
import { useExplorerStore, type RenderToggles } from '@/state/explorerSlice'

const TOGGLE_DEFS: Array<{ key: keyof RenderToggles; label: string }> = [
  { key: 'flake_bbox', label: 'Flake bbox' },
  { key: 'flake_outline', label: 'Flake outline' },
  { key: 'island_bbox', label: 'Island bbox' },
  { key: 'island_outline', label: 'Island outline' },
]

export function RenderTogglesPanel() {
  const toggles = useExplorerStore((s) => s.renderToggles)
  const toggleRender = useExplorerStore((s) => s.toggleRender)
  return (
    <fieldset aria-label="render toggles">
      <legend>Render toggles</legend>
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px',
      }}>
        {TOGGLE_DEFS.map(({ key, label }) => (
          <label key={key}>
            <input
              type="checkbox"
              aria-label={label.toLowerCase()}
              checked={toggles[key]}
              onChange={() => toggleRender(key)}
            />
            {label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}
