// web/src/components/selector/CommitButton.tsx
import { useSelectorStore } from '@/state/selectorSlice'
import { useSelectorCommit } from '@/hooks/useSelectorCommit'

interface CommitButtonProps {
  projectId: string
}

export function CommitButton({ projectId }: CommitButtonProps) {
  const toApiParams = useSelectorStore((s) => s.toApiParams)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)
  const mutation = useSelectorCommit(projectId)

  const onClick = () => {
    const lasso = selectedIds.size > 0 ? Array.from(selectedIds) : null
    mutation.mutate({ params: toApiParams(), lasso_ids: lasso })
  }

  return (
    <div>
      <button
        data-testid="selector-commit-submit"
        onClick={onClick}
        disabled={mutation.isPending}
        style={{ background: '#16a34a', color: 'white', padding: '6px 12px' }}
      >
        {mutation.isPending ? 'Committing...' : 'Commit selection'}
      </button>
      {mutation.data && (
        <div data-testid="commit-summary" style={{ marginTop: 4, fontSize: 12 }}>
          Committed {mutation.data.n_committed} / {mutation.data.total_count} domains
        </div>
      )}
      {mutation.isError && (
        <div role="alert" style={{ color: 'red', marginTop: 4 }}>
          {mutation.error?.message}
        </div>
      )}
    </div>
  )
}
