import { useMemo } from 'react'
import { useClusteringStore } from '@/state/clusteringSlice'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import { ClusterRow } from './ClusterRow'

interface Props {
  labels: LabelsJson
  assignments: AssignmentsRows
}

export function PerClusterThresholdPanel({ labels, assignments }: Props) {
  const overrides = useClusteringStore((s) => s.perClusterThresholds)
  const reset = useClusteringStore((s) => s.resetThresholdsToDefault)

  // Pre-compute per-cluster (passCount, totalCount) under current thresholds
  const stats = useMemo(() => {
    const out: Record<number, { pass: number; total: number }> = {}
    for (const g of labels.groups) {
      out[g.id] = { pass: 0, total: 0 }
    }
    for (let i = 0; i < assignments.cluster_label.length; i++) {
      const cid = assignments.cluster_label[i]
      if (out[cid] === undefined) continue
      const t = overrides[cid] ?? labels.thresholds[String(cid)] ?? 0.5
      out[cid].total += 1
      if (assignments.max_posterior[i] >= t) out[cid].pass += 1
    }
    return out
  }, [labels, assignments, overrides])

  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h4 style={{ margin: 0 }}>Per-cluster thresholds</h4>
        <button data-testid="clustering-thresholds-reset" type="button" onClick={reset} style={{ fontSize: 12 }}>
          Reset
        </button>
      </header>
      <div>
        {labels.groups.map((g) => (
          <ClusterRow
            key={g.id}
            clusterId={g.id}
            clusterName={g.name}
            passCount={stats[g.id]?.pass ?? 0}
            totalCount={stats[g.id]?.total ?? 0}
          />
        ))}
      </div>
    </section>
  )
}
