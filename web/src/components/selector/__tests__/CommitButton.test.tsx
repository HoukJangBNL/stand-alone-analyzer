// web/src/components/selector/__tests__/CommitButton.test.tsx
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { CommitButton } from '@/components/selector/CommitButton'

function wrap(node: import('react').ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('CommitButton', () => {
  it('POSTs to /selector/commit and shows summary', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            output_path: '/p/03_selector/selection.parquet',
            n_committed: 5,
            n_filter_accepted: 7,
            n_lasso: 0,
            total_count: 10,
            params_hash: 'sha256:abc',
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )

    wrap(<CommitButton projectId="local" />)
    fireEvent.click(screen.getByRole('button', { name: /Commit/ }))
    await waitFor(() => expect(screen.getByTestId('commit-summary')).not.toBeNull())
    expect(screen.getByTestId('commit-summary').textContent).toContain('5')
  })
})
