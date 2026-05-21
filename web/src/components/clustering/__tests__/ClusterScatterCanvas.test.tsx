// web/src/components/clustering/__tests__/ClusterScatterCanvas.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { ClusterScatterCanvas } from '@/components/clustering/ClusterScatterCanvas'
import type { DomainStats } from '@/api/selector'
import type { AssignmentsRows } from '@/api/clustering'

vi.mock('react-plotly.js', () => ({
  default: (props: any) => {
    ;(globalThis as any).__plotlyProps = props
    return <div data-testid="plotly-mock" />
  },
}))

const stats: DomainStats = {
  flake_ids: [1, 2, 3, 4],
  mean_r: [10, 20, 30, 40], mean_g: [10, 20, 30, 40], mean_b: [10, 20, 30, 40],
  std_r: [1, 2, 3, 4], std_g: [1, 2, 3, 4], std_b: [1, 2, 3, 4],
  areas: [100, 200, 300, 400],
}
const assignments: AssignmentsRows = {
  domain_id: [1, 2, 3, 4],
  cluster_label: [0, 1, 0, -1],
  max_posterior: [0.9, 0.8, 0.7, 0.0],
}

describe('ClusterScatterCanvas', () => {
  it('renders one trace whose marker.color is per-cluster (palette[0], palette[1], palette[0], gray)', () => {
    render(<ClusterScatterCanvas stats={stats} assignments={assignments} />)
    const props = (globalThis as any).__plotlyProps
    const colors = props.data[0].marker.color as string[]
    expect(colors[0]).toBe('#1f77b4')        // palette[0]
    expect(colors[1]).toBe('#ff7f0e')        // palette[1]
    expect(colors[2]).toBe('#1f77b4')        // palette[0]
    expect(colors[3]).toBe('#9e9e9e')        // NEUTRAL_GRAY (noise)
  })

  it('falls back to neutral-gray for all points when assignments=null (pre-fit)', () => {
    render(<ClusterScatterCanvas stats={stats} assignments={null} />)
    const props = (globalThis as any).__plotlyProps
    const colors = props.data[0].marker.color as string[]
    expect(new Set(colors)).toEqual(new Set(['#9e9e9e']))
  })
})
