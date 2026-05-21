// web/src/components/clustering/ClusterSizeBarChart.tsx
import { useMemo } from 'react'
import Plot from 'react-plotly.js'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import { colorForCluster } from '@/lib/clusterColors'

interface Props {
  labels: LabelsJson
  assignments: AssignmentsRows
}

export function ClusterSizeBarChart({ labels, assignments }: Props) {
  const { x, y, colors } = useMemo(() => {
    const counts = new Map<number, number>()
    for (const g of labels.groups) counts.set(g.id, 0)
    for (const c of assignments.cluster_label) {
      if (counts.has(c)) counts.set(c, (counts.get(c) ?? 0) + 1)
    }
    const ordered = labels.groups.map((g) => g)
    return {
      x: ordered.map((g) => g.name),
      y: ordered.map((g) => counts.get(g.id) ?? 0),
      colors: ordered.map((g) => colorForCluster(g.id)),
    }
  }, [labels, assignments])

  return (
    <Plot
      data={[{ type: 'bar' as const, x, y, marker: { color: colors } }]}
      layout={{
        title: { text: 'Cluster sizes' },
        xaxis: { title: { text: 'Cluster' } },
        yaxis: { title: { text: 'Count' } },
        margin: { t: 30, r: 10, b: 40, l: 40 },
        autosize: true,
        height: 220,
      }}
      style={{ width: '100%', height: 220 }}
      useResizeHandler
    />
  )
}
