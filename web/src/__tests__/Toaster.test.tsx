// web/src/__tests__/Toaster.test.tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { toast } from 'sonner'
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

describe('<App> mounts Sonner <Toaster>', () => {
  it('renders a toast region and surfaces toast.error messages', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})) // never resolves
    )
    window.history.pushState({}, '', '/projects/local/scans/11/compute')

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    render(
      <QueryClientProvider client={qc}>
        <App />
      </QueryClientProvider>
    )

    // Sonner mounts a region with role="region" and aria-label="Notifications"
    const region = await screen.findByRole('region', { name: /notifications/i })
    expect(region).not.toBeNull()

    act(() => {
      toast.error('hello-from-test')
    })

    expect(await screen.findByText('hello-from-test')).not.toBeNull()
  })
})
