// web/src/components/explorer/__tests__/ExplorerRightRail.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { ExplorerRightRail } from '../ExplorerRightRail'
import { resetExplorerStore } from '@/state/explorerSlice'

const wrap = (ui: React.ReactElement) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

describe('ExplorerRightRail', () => {
  beforeEach(() => {
    resetExplorerStore()
    vi.unstubAllGlobals()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ rows: [], total: 0 }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    )
  })

  it('renders the five control panels: cluster picker, neighbour filter, render toggles, LOD picker, save button', () => {
    wrap(<ExplorerRightRail projectId="local" availableLabels={['1', '2']} />)
    // The composer wrapper itself.
    expect(screen.getByTestId('explorer-right-rail')).not.toBeNull()
    // Each child uses an aria-label region; assert by aria.
    expect(screen.getByRole('region', { name: /cluster picker/i })).not.toBeNull()
    expect(screen.getByRole('group', { name: /neighbor filter/i })).not.toBeNull()
    expect(screen.getByRole('group', { name: /render toggles/i })).not.toBeNull()
    expect(screen.getByRole('group', { name: /lod picker/i })).not.toBeNull()
    expect(screen.getByRole('button', { name: /Save Explorer state/i })).not.toBeNull()
  })
})
