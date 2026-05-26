// web/src/pages/__tests__/ClusteringTab.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ClusteringTab } from '@/pages/ClusteringTab'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

vi.mock('react-plotly.js', () => ({
  default: (_props: any) => <div data-testid="plotly-mock" />,
}))

beforeEach(() => {
  vi.unstubAllGlobals()
  resetClusteringStore()
})

function wrap(node: import('react').ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

const stats = {
  flake_ids: [1, 2, 3],
  mean_r: [10, 20, 30], mean_g: [10, 20, 30], mean_b: [10, 20, 30],
  std_r: [1, 2, 3], std_g: [1, 2, 3], std_b: [1, 2, 3],
  areas: [100, 200, 300],
}

function makeFetchMock(handlers: Record<string, () => Response>) {
  return vi.fn(async (url: string) => {
    for (const [needle, make] of Object.entries(handlers)) {
      if (url.includes(needle)) return make()
    }
    return new Response(JSON.stringify({ error: { code: 'unhandled', message: url } }), {
      status: 500,
      headers: { 'content-type': 'application/json' },
    })
  })
}

describe('ClusteringTab integration', () => {
  it('pre-fit: renders authoring controls when labels=404 + no assignments', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetchMock({
        '/domain_stats': () =>
          new Response(JSON.stringify(stats), { status: 200, headers: { 'content-type': 'application/json' } }),
        '/clustering/labels': () =>
          new Response(JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/assignments': () =>
          new Response(JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/seed_groups': () =>
          new Response(JSON.stringify([]), { status: 200, headers: { 'content-type': 'application/json' } }),
      })
    )

    wrap(<ClusteringTab projectId="local" scanId={11} />)
    await waitFor(() => expect(screen.getByText(/Seed groups/)).not.toBeNull())
    expect(screen.getByRole('button', { name: /Fit GMM/ })).not.toBeNull()
    expect(screen.queryByText(/Per-cluster thresholds/)).toBeNull()
  })

  it('post-fit: renders threshold panel + commit + bar chart when labels and assignments are present', async () => {
    const labelsPayload = {
      version: 1, n_clusters: 2,
      groups: [
        { id: 0, name: 'thin', size: 2, mean_rgb: [0.1, 0.2, 0.3] },
        { id: 1, name: 'thick', size: 1, mean_rgb: [0.4, 0.5, 0.6] },
      ],
      assignments: {}, thresholds: { '0': 0.5, '1': 0.5 },
      noise_label: -1, random_state: 42, fitted_at: '2026-05-21T00:00:00Z',
    }
    const assignmentsPayload = {
      domain_id: [1, 2, 3],
      cluster_label: [0, 0, 1],
      max_posterior: [0.9, 0.8, 0.7],
    }
    vi.stubGlobal(
      'fetch',
      makeFetchMock({
        '/domain_stats': () =>
          new Response(JSON.stringify(stats), { status: 200, headers: { 'content-type': 'application/json' } }),
        '/clustering/labels': () =>
          new Response(JSON.stringify(labelsPayload), { status: 200, headers: { 'content-type': 'application/json' } }),
        '/clustering/assignments': () =>
          new Response(JSON.stringify(assignmentsPayload), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/seed_groups': () =>
          new Response(JSON.stringify([{ name: 'thin', domain_ids: [1, 2] }]), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }),
      })
    )

    wrap(<ClusteringTab projectId="local" scanId={11} />)
    await waitFor(() => expect(screen.getByText(/Per-cluster thresholds/)).not.toBeNull())
    expect(screen.getByRole('button', { name: /Commit clustering/ })).not.toBeNull()
    expect(screen.getAllByTestId('plotly-mock').length).toBe(2)
  })

  it('autoload: hydrates seed groups from /seed_groups on first mount only', async () => {
    vi.stubGlobal(
      'fetch',
      makeFetchMock({
        '/domain_stats': () =>
          new Response(JSON.stringify(stats), { status: 200, headers: { 'content-type': 'application/json' } }),
        '/clustering/labels': () =>
          new Response(JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/assignments': () =>
          new Response(JSON.stringify({ error: { code: 'clustering_not_fitted', message: 'fit first' } }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
        '/clustering/seed_groups': () =>
          new Response(
            JSON.stringify([
              { name: 'thin', domain_ids: [1, 2] },
              { name: 'thick', domain_ids: [3] },
            ]),
            { status: 200, headers: { 'content-type': 'application/json' } }
          ),
      })
    )

    wrap(<ClusteringTab projectId="local" scanId={11} />)
    await waitFor(() => expect(useClusteringStore.getState().seedGroups.length).toBe(2))
    expect(useClusteringStore.getState().seedGroups.map((g) => g.name)).toEqual(['thin', 'thick'])

    useClusteringStore.getState().clearSeedGroups()
    useClusteringStore.getState().addSeedGroup('user-edit', [99])
    fireEvent.scroll(window)
    expect(useClusteringStore.getState().seedGroups.length).toBe(1)
    expect(useClusteringStore.getState().seedGroups[0].name).toBe('user-edit')
  })
})
