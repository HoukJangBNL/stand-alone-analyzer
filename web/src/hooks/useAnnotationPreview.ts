// web/src/hooks/useAnnotationPreview.ts
import { buildPreviewUrl } from '@/api/selector'

/**
 * Returns the preview URL string. The browser handles caching via the standard
 * HTTP cache (set Cache-Control on the backend later if needed). No TanStack
 * needed — <img src=...> does the right thing here.
 */
export function useAnnotationPreview(
  projectId: string,
  domainId: number | null,
  withContour: boolean
): string | null {
  if (domainId === null) return null
  return buildPreviewUrl(projectId, domainId, withContour)
}
