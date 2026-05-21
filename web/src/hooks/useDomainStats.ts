// web/src/hooks/useDomainStats.ts
import { useQuery } from '@tanstack/react-query'
import { fetchDomainStats, type DomainStats } from '@/api/selector'

export function useDomainStats(projectId: string) {
  return useQuery<DomainStats>({
    queryKey: ['domainStats', projectId],
    queryFn: () => fetchDomainStats(projectId),
    staleTime: Infinity,
  })
}
