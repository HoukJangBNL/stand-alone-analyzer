// web/src/hooks/useExplorerFlakes.ts
import { useQuery } from '@tanstack/react-query'
import {
  fetchExplorerFlakes,
  type ExplorerFlakesQuery,
  type ExplorerFlakesResponseDto,
} from '@/api/explorer'

export function useExplorerFlakes(projectId: string, q: ExplorerFlakesQuery) {
  return useQuery<ExplorerFlakesResponseDto>({
    queryKey: [
      'explorer',
      'flakes',
      projectId,
      [...q.include].sort().join(','),
      [...q.exclude].sort().join(','),
      q.sizeMin,
      q.sizeMax,
    ],
    queryFn: () => fetchExplorerFlakes(projectId, q),
    staleTime: Infinity,
    retry: false,
  })
}
