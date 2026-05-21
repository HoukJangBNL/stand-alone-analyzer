// web/src/components/selector/Live3DToggle.tsx
import { useSelectorStore } from '@/state/selectorSlice'

export function Live3DToggle() {
  const show3D = useSelectorStore((s) => s.show3D)
  const setShow3D = useSelectorStore((s) => s.setShow3D)
  return (
    <label style={{ display: 'flex', gap: 6, alignItems: 'center', margin: '8px 0' }}>
      <input
        data-testid="selector-live3d-toggle"
        type="checkbox"
        checked={show3D}
        onChange={(e) => setShow3D(e.target.checked)}
      />
      <span>Live 3D RGB scatter</span>
    </label>
  )
}
