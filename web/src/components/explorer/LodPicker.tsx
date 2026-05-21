import { useExplorerStore, type LodChoice } from '@/state/explorerSlice'

const CHOICES: Array<{ value: LodChoice; label: string }> = [
  { value: 'auto', label: 'auto' },
  { value: 0, label: 'lod0' },
  { value: 1, label: 'lod1' },
  { value: 2, label: 'lod2' },
  { value: 3, label: 'raw' },
]

export function LodPicker() {
  const lodChoice = useExplorerStore((s) => s.lodChoice)
  const setLodChoice = useExplorerStore((s) => s.setLodChoice)
  return (
    <fieldset aria-label="lod picker">
      <legend>LOD</legend>
      {CHOICES.map((c) => (
        <label key={String(c.value)}>
          <input
            type="radio"
            name="lod-choice"
            aria-label={c.label}
            checked={lodChoice === c.value}
            onChange={() => setLodChoice(c.value)}
          />
          {c.label}
        </label>
      ))}
    </fieldset>
  )
}
