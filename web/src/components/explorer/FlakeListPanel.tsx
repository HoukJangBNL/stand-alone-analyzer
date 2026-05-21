// web/src/components/explorer/FlakeListPanel.tsx
import { useExplorerFlakes } from '@/hooks/useExplorerFlakes'
import { useExplorerStore } from '@/state/explorerSlice'

interface Props {
  projectId: string
}

export function FlakeListPanel({ projectId }: Props) {
  const include = useExplorerStore((s) => s.includeLabels)
  const exclude = useExplorerStore((s) => s.excludeLabels)
  const nf = useExplorerStore((s) => s.neighborFilter)
  const setSelectedFlakeId = useExplorerStore((s) => s.setSelectedFlakeId)

  const { data, isLoading, isError } = useExplorerFlakes(projectId, {
    include: Array.from(include),
    exclude: Array.from(exclude),
    sizeMin: nf.sizeMin,
    sizeMax: nf.sizeMax,
    isolationMin: nf.isolationMin,
    excludeBorderClipped: nf.excludeBorderClipped,
  })

  if (isLoading) return <div>Loading flakes...</div>
  if (isError) return <div>Failed to load flakes.</div>
  const flakes = data?.flakes ?? []
  if (flakes.length === 0) return <div>No flakes match the current filters.</div>

  return (
    <table data-testid="flake-list-table">
      <thead>
        <tr>
          <th>flake_id</th>
          <th>stem</th>
          <th>cluster</th>
          <th>size_px</th>
          <th>isolation_um</th>
          <th>pass</th>
        </tr>
      </thead>
      <tbody>
        {flakes.map((f) => (
          <tr key={f.flake_id} onClick={() => setSelectedFlakeId(f.flake_id)}
              style={{ cursor: 'pointer' }}>
            <td>{f.flake_id}</td>
            <td>{f.stem}</td>
            <td>{f.cluster_label ?? '-'}</td>
            <td>{f.size_px}</td>
            <td>{f.isolation_um.toFixed(2)}</td>
            <td>{f.passes_filter ? 'pass' : 'fail'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
