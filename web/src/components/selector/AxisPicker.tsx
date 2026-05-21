// web/src/components/selector/AxisPicker.tsx
import { useSelectorStore, type AvailableAxis } from '@/state/selectorSlice'

const AXES: AvailableAxis[] = ['R', 'G', 'B', 'area', 'std_r', 'std_g', 'std_b', 'sam2']

interface AxisPickerProps {
  pane: 'X' | 'Y'
}

export function AxisPicker({ pane }: AxisPickerProps) {
  const setAxis = useSelectorStore((s) => s.setAxis)
  const current = useSelectorStore((s) => (pane === 'X' ? s.axisX : s.axisY))
  const groupName = `axis-${pane}`
  return (
    <fieldset style={{ border: 'none', padding: 0, margin: '8px 0' }}>
      <legend style={{ fontSize: 12, fontWeight: 600 }}>Axis {pane}</legend>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4 }}>
        {AXES.map((a) => (
          <label key={a} style={{ fontSize: 11 }}>
            <input
              type="radio"
              name={groupName}
              checked={current === a}
              onChange={() => setAxis(pane, a)}
              aria-label={`${pane}: ${a}`}
            />{' '}
            {a}
          </label>
        ))}
      </div>
    </fieldset>
  )
}
