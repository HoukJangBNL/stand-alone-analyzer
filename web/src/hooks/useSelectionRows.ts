// web/src/hooks/useSelectionRows.ts
import { useQuery } from '@tanstack/react-query'
import { fetchSelection, type SelectionRows } from '@/api/selector'

export function useSelectionRows(projectId: string) {
  return useQuery<SelectionRows>({
    queryKey: ['selectionRows', projectId],
    queryFn: () => fetchSelection(projectId),
    staleTime: Infinity,
    retry: false,  // 404 is a normal "not committed yet" state
  })
}
