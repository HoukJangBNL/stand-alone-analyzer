// web/src/pages/ExplorerTab.tsx
// W3.3: Explorer is the future flake_analyses curation entry point. Heading
// labelled (preview) until that re-scope ships.
import { Link } from 'react-router-dom'
import { useTileManifest } from '@/hooks/useTileManifest'
import { useExplorerFlakes } from '@/hooks/useExplorerFlakes'
import { useExplorerStore } from '@/state/explorerSlice'
import { ExplorerMain } from '@/components/explorer/ExplorerMain'
import { ExplorerRightRail } from '@/components/explorer/ExplorerRightRail'
import type { ExplorerFlakeRowDto } from '@/api/explorer'

interface Props {
  projectId: string
  scanId?: number | null
}

export function ExplorerTab({ projectId, scanId = null }: Props) {
  if (!scanId) return <p data-testid="explorer-tab-no-scan">Select a scan.</p>
  return <ExplorerTabBody projectId={projectId} scanId={scanId} />
}

function ExplorerTabBody({ projectId }: { projectId: string; scanId: number }) {
  const { data: manifest, isLoading, error } = useTileManifest(projectId)
  const include = useExplorerStore((s) => s.includeLabels)
  const exclude = useExplorerStore((s) => s.excludeLabels)
  const nf = useExplorerStore((s) => s.neighborFilter)
  const flakesQuery = useExplorerFlakes(projectId, {
    include: Array.from(include),
    exclude: Array.from(exclude),
    sizeMin: nf.sizeMin,
    sizeMax: nf.sizeMax,
  })

  if (isLoading) return <div>Loading mosaic...</div>

  const code = (error as { code?: string } | null)?.code
  if (code === 'prerequisite_missing') {
    return (
      <div
        data-testid="explorer-empty-state"
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100%',
          gap: 16,
          padding: 32,
          textAlign: 'center',
        }}
      >
        <h2>Run the Clustering tab to see the Explorer.</h2>
        <p>The Explorer needs cluster labels before it can render the mosaic.</p>
        <Link to={`/projects/${projectId}/clustering`}>Open Clustering tab</Link>
      </div>
    )
  }

  if (!manifest) return <div>Failed to load mosaic.</div>

  // Build image_id → stem lookup, then bucket flake rows by stem.
  const stemByImageId = new Map<number, string>()
  for (const t of manifest.tiles) stemByImageId.set(t.image_id, t.stem)
  const flakesByStem: Record<string, ExplorerFlakeRowDto[]> = {}
  for (const r of flakesQuery.data?.rows ?? []) {
    const stem = stemByImageId.get(r.image_id)
    if (!stem) continue
    if (!flakesByStem[stem]) flakesByStem[stem] = []
    flakesByStem[stem].push(r)
  }

  // Available cluster labels are derived from row "groups" strings (e.g. "1, 2").
  // The picker treats labels as strings (matches ClusterIncludeExcludePicker contract).
  const labelSet = new Set<string>()
  for (const r of flakesQuery.data?.rows ?? []) {
    for (const tok of (r.groups ?? '').split(',')) {
      const trimmed = tok.trim()
      if (trimmed.length > 0) labelSet.add(trimmed)
    }
  }
  const availableLabels = Array.from(labelSet).sort()

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: 8 }}>
      <h2 data-testid="explorer-tab-heading" style={{ margin: 0, fontSize: 16 }}>
        Explorer <span style={{ color: '#6b7280', fontWeight: 400 }}>(preview)</span>
      </h2>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 280px',
          gap: 8,
          flex: 1,
          minHeight: 0,
        }}
      >
        <ExplorerMain projectId={projectId} manifest={manifest} flakesByStem={flakesByStem} />
        <ExplorerRightRail projectId={projectId} availableLabels={availableLabels} />
      </div>
    </div>
  )
}
