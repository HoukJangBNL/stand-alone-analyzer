// web/src/components/selector/RGBScatter3DPanel.tsx
/**
 * Display-only 3D RGB scatter — no lasso events (US-S5 AC).
 * Reuses the same selectedIds colouring rules as ScatterCanvas.
 */
import { useMemo } from 'react'
import Plot from 'react-plotly.js'
import type { DomainStats } from '@/api/selector'
import { useSelectorStore } from '@/state/selectorSlice'
import { computeAccepted } from '@/lib/applyFilter'

interface Props {
  stats: DomainStats
}

export function RGBScatter3DPanel({ stats }: Props) {
  const filter = useSelectorStore((s) => s.filter)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)

  const { data, layout } = useMemo(() => {
    const accepted = computeAccepted(stats, filter)
    const colors = stats.flake_ids.map((id) => {
      if (selectedIds.has(id)) return '#dc2626'
      if (accepted.has(id)) return '#2563eb'
      return '#9ca3af'
    })
    return {
      data: [
        {
          type: 'scatter3d' as const,
          mode: 'markers' as const,
          x: stats.mean_r,
          y: stats.mean_g,
          z: stats.mean_b,
          marker: { size: 2, color: colors },
        },
      ],
      layout: {
        scene: {
          xaxis: { title: { text: 'R' } },
          yaxis: { title: { text: 'G' } },
          zaxis: { title: { text: 'B' } },
        },
        margin: { t: 10, r: 10, b: 10, l: 10 },
      },
    }
  }, [stats, filter, selectedIds])

  return (
    <Plot
      data={data}
      layout={layout}
      style={{ width: '100%', height: 360 }}
      useResizeHandler
      // explicitly NO onSelected handler (US-S5 AC)
    />
  )
}
