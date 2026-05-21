import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { SaveExplorerStateButton } from '@/components/explorer/SaveExplorerStateButton'
import { useExplorerStore, resetExplorerStore } from '@/state/explorerSlice'

const mockToastSuccess = vi.fn()
const mockToastError = vi.fn()
vi.mock('sonner', () => ({
  toast: {
    success: (...a: unknown[]) => mockToastSuccess(...a),
    error: (...a: unknown[]) => mockToastError(...a),
  },
}))

beforeEach(() => {
  vi.unstubAllGlobals()
  mockToastSuccess.mockReset()
  mockToastError.mockReset()
  resetExplorerStore()
})

function wrap(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('SaveExplorerStateButton', () => {
  it('renders a button labeled "Save Explorer state"', () => {
    wrap(<SaveExplorerStateButton projectId="local" />)
    expect(screen.getByRole('button', { name: /Save Explorer state/i })).not.toBeNull()
  })

  it('clicking POSTs the current explorer state and shows a success toast', async () => {
    const fetchSpy = vi.fn(async (_url: string, init: RequestInit) => {
      const body = JSON.parse(init.body as string)
      expect(body.include_labels).toEqual(['thin'])
      expect(body.exclude_labels).toEqual([])
      expect(body.neighbor_filter.size_min).toBe(1)
      return new Response(JSON.stringify({
        state_path: '/tmp/explorer_state.json', selected_count: 0,
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    })
    vi.stubGlobal('fetch', fetchSpy)

    useExplorerStore.getState().addInclude('thin')
    useExplorerStore.getState().setSizeRange(1, 50)

    wrap(<SaveExplorerStateButton projectId="local" />)
    fireEvent.click(screen.getByRole('button', { name: /Save Explorer state/i }))
    await waitFor(() => expect(mockToastSuccess).toHaveBeenCalled())
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })

  it('shows an error toast on 409 prerequisite_missing', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({
        error: { code: 'prerequisite_missing', message: 'fit clustering first',
                 details: {}, request_id: 'r' },
      }), { status: 409, headers: { 'content-type': 'application/json' } })
    ))
    wrap(<SaveExplorerStateButton projectId="local" />)
    fireEvent.click(screen.getByRole('button', { name: /Save Explorer state/i }))
    await waitFor(() => expect(mockToastError).toHaveBeenCalled())
  })

  it('button is disabled while the mutation is pending', async () => {
    type ResolveFn = (r: Response) => void
    let resolveFn: ResolveFn | null = null
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      resolveFn = resolve
    })))
    wrap(<SaveExplorerStateButton projectId="local" />)
    const btn = screen.getByRole('button', { name: /Save Explorer state/i }) as HTMLButtonElement
    fireEvent.click(btn)
    await waitFor(() => expect(btn.disabled).toBe(true))
    ;(resolveFn as ResolveFn | null)?.(new Response(JSON.stringify({ state_path: '/x', selected_count: null }),
      { status: 200, headers: { 'content-type': 'application/json' } }))
    await waitFor(() => expect(btn.disabled).toBe(false))
  })
})
