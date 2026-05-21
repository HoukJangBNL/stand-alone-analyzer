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

  it('renders the surviving control panels: cluster picker, neighbour filter, save button', () => {
    wrap(<ExplorerRightRail projectId="local" availableLabels={['1', '2']} />)
    expect(screen.getByTestId('explorer-right-rail')).not.toBeNull()
    expect(screen.getByRole('region', { name: /cluster picker/i })).not.toBeNull()
    expect(screen.getByRole('group', { name: /neighbor filter/i })).not.toBeNull()
    expect(screen.getByRole('button', { name: /Save Explorer state/i })).not.toBeNull()
  })

  it('does NOT render dropped LOD picker or render toggles (W3.3)', () => {
    wrap(<ExplorerRightRail projectId="local" availableLabels={['1', '2']} />)
    expect(screen.queryByRole('group', { name: /render toggles/i })).toBeNull()
    expect(screen.queryByRole('group', { name: /lod picker/i })).toBeNull()
  })
})
