// web/src/hooks/useClusteringLabels.ts
import { useQuery } from '@tanstack/react-query'
import { fetchClusteringLabels, type LabelsJson } from '@/api/clustering'

export function useClusteringLabels(projectId: string) {
  return useQuery<LabelsJson>({
    queryKey: ['clustering', 'labels', projectId],
    queryFn: () => fetchClusteringLabels(projectId),
    staleTime: Infinity,
    retry: false,
  })
}
