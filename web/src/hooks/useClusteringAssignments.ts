// web/src/hooks/useClusteringAssignments.ts
import { useQuery } from '@tanstack/react-query'
import { fetchClusteringAssignments, type AssignmentsRows } from '@/api/clustering'

export function useClusteringAssignments(projectId: string, enabled = true) {
  return useQuery<AssignmentsRows>({
    queryKey: ['clustering', 'assignments', projectId],
    queryFn: () => fetchClusteringAssignments(projectId),
    staleTime: Infinity,
    retry: false,
    enabled,
  })
}
