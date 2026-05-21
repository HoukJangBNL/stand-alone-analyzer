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
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            rows: [
              {
                flake_id: 100,
                image_id: 10,
                domains: 1,
                groups: 'mono',
                distance: '5.00 px',
                clipped: 'no',
                pass: true,
              },
              {
                flake_id: 201,
                image_id: 20,
                domains: 2,
                groups: 'bi',
                distance: '2.50 px',
                clipped: 'yes',
                pass: false,
              },
            ],
            total: 2,
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    wrap(<FlakeListPanel projectId="local" />)
    expect(await screen.findByText('100')).not.toBeNull()
    expect(screen.getByText('201')).not.toBeNull()
    expect(screen.getByText('mono')).not.toBeNull()
    expect(screen.getByText('bi')).not.toBeNull()
  })

  it('shows "No flakes match the current filters." when the result is empty', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ rows: [], total: 0 }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    )
    wrap(<FlakeListPanel projectId="local" />)
    expect(await screen.findByText(/No flakes match the current filters/i)).not.toBeNull()
  })

  it('writes selectedFlakeId (number) to the store when a row is clicked', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            rows: [
              {
                flake_id: 100,
                image_id: 10,
                domains: 1,
                groups: 'mono',
                distance: '5.00 px',
                clipped: 'no',
                pass: true,
              },
            ],
            total: 1,
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    wrap(<FlakeListPanel projectId="local" />)
    const row = await screen.findByText('100')
    fireEvent.click(row)
    await waitFor(() => expect(useExplorerStore.getState().selectedFlakeId).toBe(100))
  })

  it('renders pass/fail strings derived from the boolean pass field', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            rows: [
              {
                flake_id: 1,
                image_id: 1,
                domains: 1,
                groups: 'mono',
                distance: '1.00 px',
                clipped: 'no',
                pass: true,
              },
              {
                flake_id: 2,
                image_id: 1,
                domains: 1,
                groups: 'mono',
                distance: '1.00 px',
                clipped: 'no',
                pass: false,
              },
            ],
            total: 2,
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    wrap(<FlakeListPanel projectId="local" />)
    // Two matches expected for "pass": the column header + the row cell.
    const passes = await screen.findAllByText('pass')
    expect(passes.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('fail')).not.toBeNull()
  })
})
