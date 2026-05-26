// web/src/components/clustering/__tests__/ClusteringRightRail.test.tsx
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ClusteringRightRail } from '@/components/clustering/ClusteringRightRail'
import { resetClusteringStore } from '@/state/clusteringSlice'
import type { LabelsJson, AssignmentsRows } from '@/api/clustering'
import type { ReactNode } from 'react'

vi.mock('react-plotly.js', () => ({ default: () => <div /> }))

beforeEach(() => {
  resetClusteringStore()
})

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

const labels: LabelsJson = {
  version: 1, n_clusters: 1,
  groups: [{ id: 0, name: 'a', size: 1, mean_rgb: [0, 0, 0] }],
  assignments: {}, thresholds: { '0': 0.5 },
  noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
}
const assignments: AssignmentsRows = {
  domain_id: [1], cluster_label: [0], max_posterior: [0.9],
}

describe('ClusteringRightRail', () => {
  it('renders authoring controls when labels=null (pre-fit)', () => {
    wrap(<ClusteringRightRail projectId="local" scanId={1} labels={null} assignments={null} />)
    expect(screen.getByText(/Seed groups/)).not.toBeNull()
    expect(screen.getByRole('button', { name: /Fit GMM/ })).not.toBeNull()
    expect(screen.queryByText(/Per-cluster thresholds/)).toBeNull()
  })

  it('renders threshold + commit blocks when labels and assignments are present', () => {
    wrap(<ClusteringRightRail projectId="local" scanId={1} labels={labels} assignments={assignments} />)
    expect(screen.getByText(/Per-cluster thresholds/)).not.toBeNull()
    expect(screen.getByRole('button', { name: /Commit clustering/ })).not.toBeNull()
  })
})
