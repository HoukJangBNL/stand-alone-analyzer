import { toast } from 'sonner'
import { useSaveExplorerState } from '@/hooks/useSaveExplorerState'
import { useExplorerStore } from '@/state/explorerSlice'

interface Props {
  projectId: string
}

export function SaveExplorerStateButton({ projectId }: Props) {
  const m = useSaveExplorerState(projectId)
  const include = useExplorerStore((s) => s.includeLabels)
  const exclude = useExplorerStore((s) => s.excludeLabels)
  const nf = useExplorerStore((s) => s.neighborFilter)

  const onClick = async () => {
    try {
      const result = await m.mutateAsync({
        include_labels: Array.from(include),
        exclude_labels: Array.from(exclude),
        neighbor_filter: {
          size_min: nf.sizeMin,
          size_max: nf.sizeMax,
          isolation_min: nf.isolationMin,
          exclude_border_clipped: nf.excludeBorderClipped,
        },
      })
      toast.success(`Saved (${result.selected_count ?? 0} flakes)`)
    } catch (e: unknown) {
      const msg = (e as { message?: string })?.message ?? 'Save failed'
      toast.error(msg)
    }
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={m.isPending}
    >
      Save Explorer state
    </button>
  )
}
