// web/src/components/explorer/__tests__/FlakeListPanel.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { FlakeListPanel } from '../FlakeListPanel'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe('FlakeListPanel', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
  })

  it('renders one row per flake from /explorer/flakes', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        flakes: [
          { flake_id: 'A:0', stem: 'A', cluster_label: 1, size_px: 120,
            isolation_um: 5.0, passes_filter: true, border_clipped: false },
          { flake_id: 'B:1', stem: 'B', cluster_label: 2, size_px: 80,
            isolation_um: 2.5, passes_filter: false, border_clipped: true },
        ],
        total: 2,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<FlakeListPanel projectId="local" />)
    expect(await screen.findByText('A:0')).not.toBeNull()
    expect(screen.getByText('B:1')).not.toBeNull()
  })

  it('shows "No flakes match the current filters." when the result is empty', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ flakes: [], total: 0 }),
        { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<FlakeListPanel projectId="local" />)
    expect(await screen.findByText(/No flakes match the current filters/i)).not.toBeNull()
  })

  it('writes selectedFlakeId to the store when a row is clicked', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        flakes: [
          { flake_id: 'A:0', stem: 'A', cluster_label: 1, size_px: 120,
            isolation_um: 5.0, passes_filter: true, border_clipped: false },
        ],
        total: 1,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<FlakeListPanel projectId="local" />)
    const row = await screen.findByText('A:0')
    fireEvent.click(row)
    await waitFor(() =>
      expect(useExplorerStore.getState().selectedFlakeId).toBe('A:0')
    )
  })
})
