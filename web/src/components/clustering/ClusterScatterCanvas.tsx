// web/src/components/clustering/ClusterScatterCanvas.tsx
import { useMemo } from 'react'
import Plot from 'react-plotly.js'
import type { DomainStats } from '@/api/selector'
import type { AssignmentsRows } from '@/api/clustering'
import { useClusteringStore } from '@/state/clusteringSlice'
import { useBrushModeStore } from '@/components/selector/BrushingControls'
import { downsampleIndices } from '@/lib/downsample'
import { CLUSTER_PALETTE, NEUTRAL_GRAY, colorForCluster } from '@/lib/clusterColors'
import type { AvailableAxis } from '@/state/selectorSlice'

interface Props {
  stats: DomainStats
  assignments: AssignmentsRows | null
}

const MAX_POINTS = 5000

function pickColumn(stats: DomainStats, axis: AvailableAxis): number[] {
  switch (axis) {
    case 'R': return stats.mean_r
    case 'G': return stats.mean_g
    case 'B': return stats.mean_b
    case 'area': return stats.areas
    case 'std_r': return stats.std_r
    case 'std_g': return stats.std_g
    case 'std_b': return stats.std_b
    case 'sam2': return stats.sam2 ?? new Array(stats.flake_ids.length).fill(0)
  }
}

export function ClusterScatterCanvas({ stats, assignments }: Props) {
  const axisX = useClusteringStore((s) => s.axisX)
  const axisY = useClusteringStore((s) => s.axisY)
  const overrides = useClusteringStore((s) => s.perClusterThresholds)
  const editingGroupId = useClusteringStore((s) => s.editingGroupId)
  const seedGroups = useClusteringStore((s) => s.seedGroups)
  const selectedIds = useClusteringStore((s) => s.brushing.selectedIds)
  const applyLasso = useClusteringStore((s) => s.applyLasso)
  const setFocusId = useClusteringStore((s) => s.setFocusId)
  const mode = useBrushModeStore((s) => s.mode)

  const editingMembers = useMemo<Set<number>>(() => {
    if (!editingGroupId) return new Set()
    const g = seedGroups.find((x) => x.id === editingGroupId)
    return g ? new Set(g.member_ids) : new Set()
  }, [editingGroupId, seedGroups])

  const { data, layout } = useMemo(() => {
    const n = stats.flake_ids.length
    const idxs = downsampleIndices(n, stats.flake_ids, MAX_POINTS, selectedIds)
    const xCol = pickColumn(stats, axisX)
    const yCol = pickColumn(stats, axisY)

    const lookup = new Map<number, { c: number; p: number }>()
    if (assignments) {
      for (let i = 0; i < assignments.domain_id.length; i++) {
        lookup.set(assignments.domain_id[i], {
          c: assignments.cluster_label[i],
          p: assignments.max_posterior[i],
        })
      }
    }

    const x = idxs.map((i) => xCol[i])
    const y = idxs.map((i) => yCol[i])
    const ids = idxs.map((i) => stats.flake_ids[i])
    const colors = ids.map((id) => {
      const r = lookup.get(id)
      if (!r) return NEUTRAL_GRAY
      const t = overrides[r.c] ?? 0.5
      if (r.c < 0 || r.p < t) return NEUTRAL_GRAY
      return colorForCluster(r.c)
    })

    const lineColors = ids.map((id) => (editingMembers.has(id) ? '#f97316' : 'rgba(0,0,0,0)'))
    const lineWidths = ids.map((id) => (editingMembers.has(id) ? 2 : 0))

    return {
      data: [
        {
          type: 'scattergl' as const,
          mode: 'markers' as const,
          x,
          y,
          customdata: ids,
          marker: { size: 5, color: colors, line: { color: lineColors, width: lineWidths } },
          hovertemplate: 'id=%{customdata}<br>x=%{x}<br>y=%{y}<extra></extra>',
        },
      ],
      layout: {
        xaxis: { title: { text: axisX } },
        yaxis: { title: { text: axisY } },
        dragmode: 'lasso' as const,
        margin: { t: 10, r: 10, b: 40, l: 40 },
        hovermode: 'closest' as const,
        autosize: true,
      },
    }
  }, [stats, assignments, axisX, axisY, overrides, selectedIds, editingMembers])

  void CLUSTER_PALETTE
  void NEUTRAL_GRAY

  return (
    <Plot
      data={data}
      layout={layout}
      style={{ width: '100%', height: 480 }}
      useResizeHandler
      onSelected={(ev: any) => {
        if (!ev?.points) return
        const ids = ev.points.map((p: any) => p.customdata as number)
        applyLasso(ids, mode)
      }}
      onClick={(ev: any) => {
        const pt = ev?.points?.[0]
        if (pt?.customdata !== undefined) setFocusId(pt.customdata as number)
      }}
    />
  )
}
