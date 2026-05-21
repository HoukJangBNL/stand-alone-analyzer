import { useEffect, useRef, useState } from 'react'
import { useClusteringStore } from '@/state/clusteringSlice'
import { CLUSTER_PALETTE } from '@/lib/clusterColors'

interface Props {
  clusterId: number
  clusterName: string
  passCount: number
  totalCount: number
}

const DEBOUNCE_MS = 100 // per design §2.1: recolor budget 300ms → tighter than Selector's 200ms

export function ClusterRow({ clusterId, clusterName, passCount, totalCount }: Props) {
  const stored = useClusteringStore((s) => s.perClusterThresholds[clusterId])
  const setThreshold = useClusteringStore((s) => s.setThreshold)

  const initial = stored ?? 0.5
  const [local, setLocal] = useState<number>(initial)

  // Sync from store when external resets occur
  useEffect(() => {
    setLocal(stored ?? 0.5)
  }, [stored])

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = parseFloat(e.target.value)
    setLocal(v)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setThreshold(clusterId, v), DEBOUNCE_MS)
  }

  const swatch = CLUSTER_PALETTE[clusterId % CLUSTER_PALETTE.length]

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '16px 1fr 1fr 90px',
        alignItems: 'center',
        gap: 6,
        padding: '4px 0',
      }}
    >
      <span
        data-testid={`cluster-swatch-${clusterId}`}
        style={{ width: 14, height: 14, background: swatch, borderRadius: 2, display: 'inline-block' }}
      />
      <span style={{ fontSize: 12 }}>{clusterName}</span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={local}
        onChange={handleChange}
        aria-label={`threshold cluster ${clusterId}`}
      />
      <span style={{ fontSize: 12, color: '#444' }}>
        {passCount} / {totalCount} pass ({local.toFixed(2)})
      </span>
    </div>
  )
}
