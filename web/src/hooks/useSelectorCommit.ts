// web/src/hooks/useSelectorCommit.ts
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  postCommit,
  type CommitRequest,
  type CommitSummary,
  ApiError,
} from '@/api/selector'

export function useSelectorCommit(projectId: string) {
  const qc = useQueryClient()
  return useMutation<CommitSummary, ApiError, CommitRequest>({
    mutationFn: (body) => postCommit(projectId, body),
    onSuccess: () => {
      // selection.parquet just changed; invalidate readers that depend on it.
      qc.invalidateQueries({ queryKey: ['selectionRows', projectId] })
      qc.invalidateQueries({ queryKey: ['manifest', projectId] })
    },
  })
}
