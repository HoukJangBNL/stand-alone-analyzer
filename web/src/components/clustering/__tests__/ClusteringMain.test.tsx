// web/src/components/clustering/__tests__/ClusteringMain.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ClusteringMain } from '@/components/clustering/ClusteringMain'
import type { DomainStats } from '@/api/selector'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'

vi.mock('react-plotly.js', () => ({ default: () => <div data-testid="plotly-mock" /> }))

const stats: DomainStats = {
  flake_ids: [1], mean_r: [0], mean_g: [0], mean_b: [0],
  std_r: [0], std_g: [0], std_b: [0], areas: [1],
}
const labels: LabelsJson = {
  version: 1, n_clusters: 1,
  groups: [{ id: 0, name: 'a', size: 1, mean_rgb: [0, 0, 0] }],
  assignments: {}, thresholds: { '0': 0.5 },
  noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
}
const assignments: AssignmentsRows = { domain_id: [1], cluster_label: [0], max_posterior: [0.9] }

describe('ClusteringMain', () => {
  it('renders only the scatter pre-fit', () => {
    render(<ClusteringMain stats={stats} labels={null} assignments={null} />)
    expect(screen.getAllByTestId('plotly-mock').length).toBe(1)
  })

  it('renders scatter + bar chart post-fit', () => {
    render(<ClusteringMain stats={stats} labels={labels} assignments={assignments} />)
    expect(screen.getAllByTestId('plotly-mock').length).toBe(2)
  })
})
