// web/src/components/clustering/__tests__/ClusterSizeBarChart.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { ClusterSizeBarChart } from '@/components/clustering/ClusterSizeBarChart'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'

vi.mock('react-plotly.js', () => ({
  default: (props: any) => {
    ;(globalThis as any).__plotlyBarProps = props
    return <div data-testid="plotly-bar-mock" />
  },
}))

const labels: LabelsJson = {
  version: 1, n_clusters: 2,
  groups: [
    { id: 0, name: 'thin', size: 0, mean_rgb: [0, 0, 0] },
    { id: 1, name: 'thick', size: 0, mean_rgb: [0, 0, 0] },
  ],
  assignments: {}, thresholds: { '0': 0.5, '1': 0.5 },
  noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
}
const assignments: AssignmentsRows = {
  domain_id: [1, 2, 3, 4],
  cluster_label: [0, 0, 1, -1],
  max_posterior: [0.9, 0.4, 0.8, 0.0],
}

describe('ClusterSizeBarChart', () => {
  it('renders one bar per cluster with counts pre-threshold', () => {
    render(<ClusterSizeBarChart labels={labels} assignments={assignments} />)
    const props = (globalThis as any).__plotlyBarProps
    const trace = props.data[0]
    expect(trace.x).toEqual(['thin', 'thick'])
    expect(trace.y).toEqual([2, 1])
  })
})
