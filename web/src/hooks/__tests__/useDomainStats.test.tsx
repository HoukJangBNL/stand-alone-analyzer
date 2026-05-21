// web/src/hooks/__tests__/useDomainStats.test.tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { useDomainStats } from '@/hooks/useDomainStats'

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: import('react').ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('useDomainStats', () => {
  it('fetches domain stats and exposes typed arrays', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            flake_ids: [1, 2],
            mean_r: [10, 20], mean_g: [30, 40], mean_b: [50, 60],
            std_r: [1, 2], std_g: [3, 4], std_b: [5, 6],
            areas: [100, 200],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    const { result } = renderHook(() => useDomainStats('local'), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.flake_ids).toEqual([1, 2])
  })
})
