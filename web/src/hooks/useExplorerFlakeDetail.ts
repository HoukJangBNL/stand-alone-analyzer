// web/src/hooks/useExplorerFlakeDetail.ts
import { useQuery } from '@tanstack/react-query'
import {
  fetchExplorerFlakeDetail,
  type ExplorerFlakeDetailDto,
} from '@/api/explorer'

export function useExplorerFlakeDetail(projectId: string, flakeId: number | null) {
  return useQuery<ExplorerFlakeDetailDto>({
    queryKey: ['explorer', 'flake', projectId, flakeId],
    queryFn: () => fetchExplorerFlakeDetail(projectId, flakeId as number),
    enabled: flakeId !== null,
    staleTime: Infinity,
    retry: false,
  })
}
