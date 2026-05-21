// web/src/__tests__/App.explorer-route.test.tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from '@/App'

vi.mock('openseadragon', () => ({
  default: vi.fn(() => ({
    open: vi.fn(),
    destroy: vi.fn(),
    addOverlay: vi.fn(),
    removeOverlay: vi.fn(),
    clearOverlays: vi.fn(),
    addHandler: vi.fn(),
    world: { getItemCount: () => 0, getItemAt: () => ({ setOpacity: vi.fn() }) },
    viewport: {
      viewerElementToViewportCoordinates: () => ({ x: 0, y: 0 }),
      viewportToImageCoordinates: () => ({ x: 0, y: 0 }),
    },
    element: document.createElement('div'),
  })),
}))

describe('App route registration — Explorer', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    vi.stubGlobal(
      'fetch',
      vi.fn(
        () =>
          new Promise(() => {
            /* never resolves so the manifest stays in loading state */
          })
      )
    )
    window.history.pushState({}, '', '/projects/local/explorer')
  })

  it('renders ExplorerTab when navigating to /projects/local/explorer', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    render(
      <QueryClientProvider client={qc}>
        <App />
      </QueryClientProvider>
    )
    // Suspense fallback resolves; ExplorerTab then shows "Loading mosaic..."
    expect(await screen.findByText(/Loading mosaic/i)).not.toBeNull()
  })
})
