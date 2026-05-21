// web/src/hooks/useClusteringSeedGroups.ts
import { useQuery } from '@tanstack/react-query'
import { fetchClusteringSeedGroups, type SeedGroupDto } from '@/api/clustering'

export function useClusteringSeedGroups(projectId: string) {
  return useQuery<SeedGroupDto[]>({
    queryKey: ['clustering', 'seed_groups', projectId],
    queryFn: () => fetchClusteringSeedGroups(projectId),
    staleTime: Infinity,
    retry: false,
  })
}
