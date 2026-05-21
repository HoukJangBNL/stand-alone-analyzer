import { useExplorerStore } from '@/state/explorerSlice'

interface Props {
  availableLabels: string[]
}

export function ClusterIncludeExcludePicker({ availableLabels }: Props) {
  const include = useExplorerStore((s) => s.includeLabels)
  const exclude = useExplorerStore((s) => s.excludeLabels)
  const addInclude = useExplorerStore((s) => s.addInclude)
  const removeInclude = useExplorerStore((s) => s.removeInclude)
  const addExclude = useExplorerStore((s) => s.addExclude)
  const removeExclude = useExplorerStore((s) => s.removeExclude)

  if (availableLabels.length === 0) {
    return (
      <div role="region" aria-label="cluster picker">
        <em>No clusters available. Commit clustering first.</em>
      </div>
    )
  }

  const conflicts = availableLabels.filter((n) => include.has(n) && exclude.has(n))

  return (
    <div role="region" aria-label="cluster picker">
      <fieldset>
        <legend>Include</legend>
        {availableLabels.map((n) => (
          <label key={`inc-${n}`}>
            <input
              type="checkbox"
              aria-label={`Include ${n}`}
              checked={include.has(n)}
              onChange={(e) => e.target.checked ? addInclude(n) : removeInclude(n)}
            />
            {n}
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>Exclude</legend>
        {availableLabels.map((n) => (
          <label key={`exc-${n}`}>
            <input
              type="checkbox"
              aria-label={`Exclude ${n}`}
              checked={exclude.has(n)}
              onChange={(e) => e.target.checked ? addExclude(n) : removeExclude(n)}
            />
            {n}
          </label>
        ))}
      </fieldset>
      {conflicts.length > 0 && (
        <span style={{ color: '#C62828', fontStyle: 'italic' }}>
          Conflict: {conflicts.join(', ')} in both columns ignored
        </span>
      )}
    </div>
  )
}
