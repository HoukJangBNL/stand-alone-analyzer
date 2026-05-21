// web/src/hooks/useExplorerGrid.ts
import { useQuery } from '@tanstack/react-query'
import { fetchExplorerGrid, type TileManifestDto } from '@/api/explorer'

export function useExplorerGrid(projectId: string) {
  return useQuery<TileManifestDto>({
    queryKey: ['explorer', 'grid', projectId],
    queryFn: () => fetchExplorerGrid(projectId),
    staleTime: Infinity,
    retry: false,
  })
}
