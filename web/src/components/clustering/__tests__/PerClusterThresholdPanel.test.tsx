import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PerClusterThresholdPanel } from '@/components/clustering/PerClusterThresholdPanel'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'

beforeEach(() => {
  resetClusteringStore()
})

const labels: LabelsJson = {
  version: 1,
  n_clusters: 2,
  groups: [
    { id: 0, name: 'thin', size: 50, mean_rgb: [0.1, 0.2, 0.3] },
    { id: 1, name: 'thick', size: 30, mean_rgb: [0.4, 0.5, 0.6] },
  ],
  assignments: {},
  thresholds: { '0': 0.5, '1': 0.5 },
  noise_label: -1,
  random_state: 42,
  fitted_at: '2026-05-21T00:00:00Z',
}
const assignments: AssignmentsRows = {
  domain_id: [1, 2, 3, 4],
  cluster_label: [0, 0, 1, 1],
  max_posterior: [0.9, 0.4, 0.8, 0.3],
}

describe('PerClusterThresholdPanel', () => {
  it('renders one ClusterRow per group', () => {
    render(<PerClusterThresholdPanel labels={labels} assignments={assignments} />)
    expect(screen.getByText(/thin/)).not.toBeNull()
    expect(screen.getByText(/thick/)).not.toBeNull()
  })

  it('"Reset" sets all thresholds back to default', () => {
    useClusteringStore.getState().setThreshold(0, 0.9)
    render(<PerClusterThresholdPanel labels={labels} assignments={assignments} />)
    const btn = screen.getByRole('button', { name: /Reset/ })
    btn.click()
    expect(useClusteringStore.getState().perClusterThresholds).toEqual({})
  })

  it('"K/N pass" reflects domains where max_posterior >= per-cluster threshold', () => {
    render(<PerClusterThresholdPanel labels={labels} assignments={assignments} />)
    // Default threshold 0.5; cluster 0 has posteriors [0.9, 0.4] → 1 pass; cluster 1 has [0.8, 0.3] → 1 pass
    expect(screen.getAllByText(/1 \/ 2 pass/).length).toBeGreaterThanOrEqual(2)
  })
})
