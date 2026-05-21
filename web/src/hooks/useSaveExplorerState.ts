// web/src/hooks/useSaveExplorerState.ts
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  saveExplorerState,
  type SaveExplorerStateBody,
  type SaveExplorerStateResultDto,
} from '@/api/explorer'

export function useSaveExplorerState(projectId: string) {
  const qc = useQueryClient()
  return useMutation<SaveExplorerStateResultDto, unknown, SaveExplorerStateBody>({
    mutationKey: ['explorer', 'save_state', projectId],
    mutationFn: (body) => saveExplorerState(projectId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['explorer', 'state', projectId] })
    },
  })
}
