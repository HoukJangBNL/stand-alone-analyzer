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

  // The server-side ExplorerFlakesQuery only supports include/exclude/sizeMin/
  // sizeMax. isolationMin and excludeBorderClipped live in the slice but are
  // applied client-side (or by a future API revision); do not pass them here.
  const { data, isLoading, isError } = useExplorerFlakes(projectId, {
    include: Array.from(include),
    exclude: Array.from(exclude),
    sizeMin: nf.sizeMin,
    sizeMax: nf.sizeMax,
  })

  if (isLoading) return <div>Loading flakes...</div>
  if (isError) return <div>Failed to load flakes.</div>
  const rows = data?.rows ?? []
  if (rows.length === 0) return <div>No flakes match the current filters.</div>

  return (
    <table data-testid="flake-list-table">
      <thead>
        <tr>
          <th>flake_id</th>
          <th>image_id</th>
          <th>domains</th>
          <th>groups</th>
          <th>distance</th>
          <th>clipped</th>
          <th>pass</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((f) => (
          <tr
            key={f.flake_id}
            onClick={() => setSelectedFlakeId(f.flake_id)}
            style={{ cursor: 'pointer' }}
          >
            <td>{f.flake_id}</td>
            <td>{f.image_id}</td>
            <td>{f.domains}</td>
            <td>{f.groups}</td>
            <td>{f.distance}</td>
            <td>{f.clipped}</td>
            <td>{f.pass ? 'pass' : 'fail'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
