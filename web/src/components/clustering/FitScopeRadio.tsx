import { useClusteringStore } from '@/state/clusteringSlice'

export function FitScopeRadio() {
  const fitScope = useClusteringStore((s) => s.fitScope)
  const setFitScope = useClusteringStore((s) => s.setFitScope)
  return (
    <fieldset style={{ border: '1px solid #ddd', borderRadius: 4, padding: 8 }}>
      <legend style={{ fontSize: 12, fontWeight: 600 }}>Fit scope</legend>
      <label style={{ display: 'block' }}>
        <input
          data-testid="clustering-fit-scope-seeds"
          type="radio"
          name="fit-scope"
          value="seeds"
          checked={fitScope === 'seeds'}
          onChange={() => setFitScope('seeds')}
        />
        {' '}Seeds only
      </label>
      <label style={{ display: 'block' }}>
        <input
          data-testid="clustering-fit-scope-all"
          type="radio"
          name="fit-scope"
          value="all_selected"
          checked={fitScope === 'all_selected'}
          onChange={() => setFitScope('all_selected')}
        />
        {' '}All selected (selector subset)
      </label>
    </fieldset>
  )
}
