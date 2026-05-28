// web/src/components/run/__tests__/GpuPoolBadge.test.tsx
/**
 * GpuPoolBadge — small status indicator at the top of ComputeTab. Polls
 * `GET /api/v1/gpu/status` every 60s via TanStack Query and renders one
 * of five states. Hover/click expands a tooltip with detail, relative
 * checked_at, and (when available) the spot-price table.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import * as gpuApi from '@/api/gpu'
import type { GpuPoolStatus } from '@/api/gpu'
import { GpuPoolBadge } from '@/components/run/GpuPoolBadge'

vi.mock('@/api/gpu', async () => {
  const actual = await vi.importActual<typeof gpuApi>('@/api/gpu')
  return { ...actual, fetchGpuStatus: vi.fn() }
})

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  vi.mocked(gpuApi.fetchGpuStatus).mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('GpuPoolBadge', () => {
  it('renders running state with green color and "Active" label', async () => {
    const status: GpuPoolStatus = {
      state: 'running',
      detail: 'g6e.48xlarge running in us-east-2b',
      checked_at: '2026-05-28T12:00:00Z',
      spot_prices_usd_per_hr: null,
    }
    vi.mocked(gpuApi.fetchGpuStatus).mockResolvedValue(status)

    wrap(<GpuPoolBadge />)

    const badge = await screen.findByTestId('gpu-pool-badge')
    await waitFor(() => {
      expect(badge.getAttribute('data-state')).toBe('running')
    })
    expect(badge.textContent).toContain('Active')
    // Color tokens: green for running. jsdom serializes hex → rgb(...).
    expect(badge.style.color).toBe('rgb(22, 101, 52)') // #166534
    expect(badge.style.backgroundColor).toBe('rgb(220, 252, 231)') // #dcfce7
  })

  it('renders ready state and shows spot prices table inside the tooltip on click', async () => {
    const status: GpuPoolStatus = {
      state: 'ready',
      detail: 'Spot pool healthy; no instance running.',
      checked_at: '2026-05-28T12:00:00Z',
      spot_prices_usd_per_hr: { 'us-east-2a': 4.61, 'us-east-2b': 4.55 },
    }
    vi.mocked(gpuApi.fetchGpuStatus).mockResolvedValue(status)

    wrap(<GpuPoolBadge />)

    const badge = await screen.findByTestId('gpu-pool-badge')
    await waitFor(() => {
      expect(badge.getAttribute('data-state')).toBe('ready')
    })
    expect(badge.textContent).toContain('Ready')

    // Tooltip starts hidden; click to open.
    expect(screen.queryByTestId('gpu-pool-badge-tooltip')).toBeNull()
    fireEvent.click(badge)

    const tooltip = await screen.findByTestId('gpu-pool-badge-tooltip')
    expect(tooltip.textContent).toContain('Spot pool healthy')
    // Spot price table renders one row per AZ.
    expect(screen.getByTestId('gpu-pool-badge-price-us-east-2a').textContent).toContain('4.61')
    expect(screen.getByTestId('gpu-pool-badge-price-us-east-2b').textContent).toContain('4.55')
  })

  it('renders unavailable_capacity with red color and "Capacity Unavailable" label', async () => {
    vi.mocked(gpuApi.fetchGpuStatus).mockResolvedValue({
      state: 'unavailable_capacity',
      detail: 'Spot pool not publishing prices (likely InsufficientCapacity).',
      checked_at: '2026-05-28T12:00:00Z',
      spot_prices_usd_per_hr: null,
    })

    wrap(<GpuPoolBadge />)

    const badge = await screen.findByTestId('gpu-pool-badge')
    await waitFor(() => {
      expect(badge.getAttribute('data-state')).toBe('unavailable_capacity')
    })
    expect(badge.textContent).toContain('Capacity Unavailable')
    // Red token (#991b1b text on #fee2e2 bg, mirrors Sidebar's #b91c1c family).
    expect(badge.style.color).toBe('rgb(153, 27, 27)') // #991b1b
    expect(badge.style.backgroundColor).toBe('rgb(254, 226, 226)') // #fee2e2
  })

  it('renders unknown state with gray color and "Status Unknown" label', async () => {
    vi.mocked(gpuApi.fetchGpuStatus).mockResolvedValue({
      state: 'unknown',
      detail: 'AWS describe failed.',
      checked_at: '2026-05-28T12:00:00Z',
      spot_prices_usd_per_hr: null,
    })

    wrap(<GpuPoolBadge />)

    const badge = await screen.findByTestId('gpu-pool-badge')
    await waitFor(() => {
      expect(badge.textContent).toContain('Status Unknown')
    })
    expect(badge.getAttribute('data-state')).toBe('unknown')
  })

  it('polls every 60 seconds (refetches after 60s of fake time)', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const baseStatus: GpuPoolStatus = {
      state: 'ready',
      detail: 'Spot pool healthy.',
      checked_at: '2026-05-28T12:00:00Z',
      spot_prices_usd_per_hr: null,
    }
    vi.mocked(gpuApi.fetchGpuStatus).mockResolvedValue(baseStatus)

    wrap(<GpuPoolBadge />)

    // First fetch should resolve via the auto-advancing fake clock.
    await waitFor(() => {
      expect(gpuApi.fetchGpuStatus).toHaveBeenCalledTimes(1)
    })

    // Advance the clock by the polling interval; the refetch should fire.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })

    await waitFor(() => {
      expect(gpuApi.fetchGpuStatus).toHaveBeenCalledTimes(2)
    })
  })

  it('shows relative checked_at time inside the tooltip', async () => {
    const FIXED_NOW = new Date('2026-05-28T12:00:30Z')
    vi.setSystemTime(FIXED_NOW)
    vi.mocked(gpuApi.fetchGpuStatus).mockResolvedValue({
      state: 'ready',
      detail: 'Spot pool healthy.',
      checked_at: '2026-05-28T12:00:00Z', // FIXED_NOW - 30s
      spot_prices_usd_per_hr: null,
    })

    wrap(<GpuPoolBadge />)

    const badge = await screen.findByTestId('gpu-pool-badge')
    await waitFor(() => {
      expect(badge.getAttribute('data-state')).toBe('ready')
    })
    fireEvent.click(badge)
    const tooltip = await screen.findByTestId('gpu-pool-badge-tooltip')
    expect(tooltip.textContent).toMatch(/30\s*s/i) // "30s ago" or "30 seconds ago"
    expect(tooltip.textContent?.toLowerCase()).toContain('ago')

    vi.setSystemTime(new Date()) // restore
  })
})
