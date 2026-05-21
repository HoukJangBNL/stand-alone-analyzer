// web/src/pages/__tests__/SelectorTab.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { SelectorTab } from '@/pages/SelectorTab'
import { useSelectorStore } from '@/state/selectorSlice'

vi.mock('react-plotly.js', () => ({
  default: (_props: any) => <div data-testid="plotly-mock" />,
}))

beforeEach(() => {
  vi.unstubAllGlobals()
  useSelectorStore.getState().resetFilter()
  useSelectorStore.getState().clearBrush()
})

function wrap(node: import('react').ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('SelectorTab integration', () => {
  it('loads domain stats then renders rail + main', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            flake_ids: [1, 2, 3],
            mean_r: [10, 20, 30], mean_g: [10, 20, 30], mean_b: [10, 20, 30],
            std_r: [1, 2, 3], std_g: [1, 2, 3], std_b: [1, 2, 3],
            areas: [100, 200, 300],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    wrap(<SelectorTab projectId="local" />)
    await waitFor(() => expect(screen.getByText(/Filter/)).not.toBeNull())
    expect(screen.getByTestId('plotly-mock')).not.toBeNull()
    expect(screen.getAllByText(/Commit selection/).length).toBeGreaterThan(0)
  })

  it('shows error envelope when domain_stats is missing (404)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ error: { code: 'domain_stats_not_found', message: 'Run Compute → Domain Stats first.' } }),
          { status: 404, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    wrap(<SelectorTab projectId="local" />)
    await waitFor(() => expect(screen.getByRole('alert')).not.toBeNull())
    expect(screen.getByRole('alert').textContent).toMatch(/Run Compute/)
  })

  it('row click in flake list updates focus + the preview <img>', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            flake_ids: [1, 2, 3],
            mean_r: [10, 20, 30], mean_g: [10, 20, 30], mean_b: [10, 20, 30],
            std_r: [1, 2, 3], std_g: [1, 2, 3], std_b: [1, 2, 3],
            areas: [100, 200, 300],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    wrap(<SelectorTab projectId="local" />)
    await waitFor(() => expect(screen.getByText(/Flake list/)).not.toBeNull())
    // Open the accordion
    fireEvent.click(screen.getByText(/Flake list/))
    fireEvent.click(await screen.findByTestId('flake-row-2'))
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.src).toContain('/data/annotations/2/preview')
  })
})
