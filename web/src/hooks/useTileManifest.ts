// web/src/hooks/useTileManifest.ts
import { useQuery } from '@tanstack/react-query'
import { fetchTileManifest, type TileManifestDto } from '@/api/explorer'

export function useTileManifest(projectId: string) {
  return useQuery<TileManifestDto>({
    queryKey: ['explorer', 'tile_manifest', projectId],
    queryFn: () => fetchTileManifest(projectId),
    staleTime: Infinity,
    retry: false,
  })
}
