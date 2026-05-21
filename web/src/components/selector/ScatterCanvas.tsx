/**
 * Plotly Scattergl with lasso + click events.
 * Imports react-plotly.js statically because the wrapping <SelectorTab/> is
 * itself lazy-loaded (Task 25). That gives one chunk for the whole tab.
 */
import { useMemo } from 'react'
import Plot from 'react-plotly.js'
import type { DomainStats } from '@/api/selector'
import { useSelectorStore, type AvailableAxis } from '@/state/selectorSlice'
import { useBrushModeStore } from './BrushingControls'
import { downsampleIndices } from '@/lib/downsample'
import { computeAccepted } from '@/lib/applyFilter'

interface ScatterCanvasProps {
  stats: DomainStats
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

export function ScatterCanvas({ stats }: ScatterCanvasProps) {
  const axisX = useSelectorStore((s) => s.axisX)
  const axisY = useSelectorStore((s) => s.axisY)
  const filter = useSelectorStore((s) => s.filter)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)
  const applyLasso = useSelectorStore((s) => s.applyLasso)
  const setFocusId = useSelectorStore((s) => s.setFocusId)
  const mode = useBrushModeStore((s) => s.mode)

  const { data, layout } = useMemo(() => {
    const n = stats.flake_ids.length
    const idxs = downsampleIndices(n, stats.flake_ids, MAX_POINTS, selectedIds)
    const accepted = computeAccepted(stats, filter)
    const xCol = pickColumn(stats, axisX)
    const yCol = pickColumn(stats, axisY)

    const x = idxs.map((i) => xCol[i])
    const y = idxs.map((i) => yCol[i])
    const ids = idxs.map((i) => stats.flake_ids[i])
    const colors = ids.map((id) => {
      if (selectedIds.has(id)) return '#dc2626'
      if (accepted.has(id)) return '#2563eb'
      return '#9ca3af'
    })

    return {
      data: [
        {
          type: 'scattergl' as const,
          mode: 'markers' as const,
          x,
          y,
          customdata: ids,
          marker: { size: 5, color: colors },
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
  }, [stats, axisX, axisY, filter, selectedIds])

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
        if (pt?.customdata !== undefined) {
          setFocusId(pt.customdata as number)
        }
      }}
    />
  )
}
