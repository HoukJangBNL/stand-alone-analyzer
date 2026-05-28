// web/src/components/run/GpuPoolBadge.tsx
/**
 * GpuPoolBadge — compact status indicator for the GPU spot pool feeding
 * SAM pipelines. Lives at the top of ComputeTab so the user can see at a
 * glance whether starting a run will succeed immediately, wait on a
 * launch, or hit a capacity wall.
 *
 * Polls `GET /api/v1/gpu/status` every 60s via TanStack Query (the
 * QueryClient at the app root manages cancellation on unmount). Click
 * (or focus + Enter) toggles a tooltip with `detail`, relative
 * `checked_at`, and the per-AZ spot price table when available. ESC
 * dismisses the tooltip.
 *
 * Color tokens reuse the inline-style palette already used elsewhere in
 * the codebase (e.g. Sidebar errors `#b91c1c`, gray text `#6b7280`).
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchGpuStatus, type GpuPoolState, type GpuPoolStatus } from '@/api/gpu'

const POLL_INTERVAL_MS = 60_000

interface StateStyle {
  label: string
  color: string
  background: string
  border: string
}

// Color palette mirrors existing inline-style usage in this codebase
// (Sidebar `#b91c1c` red, gray `#6b7280`, indigo `#eef2ff`). Greens/ambers
// chosen to match Tailwind-style 600/100 token pairs already in use elsewhere.
const STATE_STYLES: Record<GpuPoolState, StateStyle> = {
  running: {
    label: 'Active',
    color: '#166534',
    background: '#dcfce7',
    border: '#16a34a',
  },
  ready: {
    label: 'GPU Pool: Ready',
    color: '#166534',
    background: '#dcfce7',
    border: '#16a34a',
  },
  launching: {
    label: 'Launching…',
    color: '#92400e',
    background: '#fef3c7',
    border: '#d97706',
  },
  unavailable_capacity: {
    label: 'Capacity Unavailable',
    color: '#991b1b',
    background: '#fee2e2',
    border: '#b91c1c',
  },
  unknown: {
    label: 'Status Unknown',
    color: '#374151',
    background: '#e5e7eb',
    border: '#6b7280',
  },
}

function formatRelative(checkedAt: string): string {
  const then = Date.parse(checkedAt)
  if (Number.isNaN(then)) return 'unknown time'
  const diffMs = Date.now() - then
  const diffSec = Math.max(0, Math.round(diffMs / 1000))
  if (diffSec < 60) return `${diffSec}s ago`
  const diffMin = Math.round(diffSec / 60)
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.round(diffMin / 60)
  return `${diffHr}h ago`
}

export function GpuPoolBadge() {
  const { data, isLoading, isError } = useQuery<GpuPoolStatus>({
    queryKey: ['gpu', 'status'],
    queryFn: fetchGpuStatus,
    refetchInterval: POLL_INTERVAL_MS,
    refetchOnWindowFocus: false,
    retry: false,
    staleTime: POLL_INTERVAL_MS,
  })
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)

  // Close tooltip on ESC; keeps keyboard reachability per a11y rule.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // Click-outside dismissal so the tooltip doesn't trap the user.
  useEffect(() => {
    if (!open) return
    const onClick = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setOpen(false)
      }
    }
    window.addEventListener('mousedown', onClick)
    return () => window.removeEventListener('mousedown', onClick)
  }, [open])

  // Loading / error fall back to the "unknown" presentation so the layout
  // never reflows.
  const state: GpuPoolState = data?.state ?? 'unknown'
  const style = STATE_STYLES[state]
  const detail = data?.detail ?? (isError ? 'Failed to fetch GPU status.' : 'Loading…')
  const ariaLabel = isLoading
    ? 'GPU pool status: loading'
    : `GPU pool status: ${style.label}. ${detail}`

  return (
    <div
      ref={containerRef}
      style={{ position: 'relative', display: 'inline-block' }}
    >
      <button
        type="button"
        data-testid="gpu-pool-badge"
        data-state={state}
        role="status"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          minWidth: 150,
          maxWidth: 220,
          padding: '4px 10px',
          fontSize: 12,
          fontWeight: 500,
          color: style.color,
          backgroundColor: style.background,
          border: `1px solid ${style.border}`,
          borderRadius: 999,
          cursor: 'pointer',
          textAlign: 'left',
          font: 'inherit',
          fontStyle: 'normal',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: 'inline-block',
            width: 8,
            height: 8,
            borderRadius: '50%',
            backgroundColor: style.border,
            flexShrink: 0,
          }}
        />
        <span
          style={{
            color: style.color,
            fontWeight: 500,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {style.label}
        </span>
      </button>

      {open && data && (
        <div
          data-testid="gpu-pool-badge-tooltip"
          role="dialog"
          aria-label="GPU pool status detail"
          style={{
            position: 'absolute',
            top: '100%',
            right: 0,
            marginTop: 4,
            minWidth: 240,
            padding: 10,
            background: '#ffffff',
            border: '1px solid #e5e7eb',
            borderRadius: 6,
            boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
            fontSize: 12,
            color: '#111827',
            zIndex: 50,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{style.label}</div>
          <div style={{ marginBottom: 6, color: '#374151' }}>{data.detail}</div>
          <div style={{ color: '#6b7280', marginBottom: 6 }}>
            Checked {formatRelative(data.checked_at)}
          </div>
          {data.spot_prices_usd_per_hr &&
            Object.keys(data.spot_prices_usd_per_hr).length > 0 && (
              <table
                data-testid="gpu-pool-badge-prices"
                style={{
                  width: '100%',
                  borderCollapse: 'collapse',
                  fontSize: 11,
                }}
              >
                <thead>
                  <tr>
                    <th
                      style={{
                        textAlign: 'left',
                        padding: '2px 4px',
                        color: '#6b7280',
                        fontWeight: 500,
                      }}
                    >
                      AZ
                    </th>
                    <th
                      style={{
                        textAlign: 'right',
                        padding: '2px 4px',
                        color: '#6b7280',
                        fontWeight: 500,
                      }}
                    >
                      $/hr
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.spot_prices_usd_per_hr).map(([az, price]) => (
                    <tr
                      key={az}
                      data-testid={`gpu-pool-badge-price-${az}`}
                    >
                      <td style={{ padding: '2px 4px' }}>{az}</td>
                      <td
                        style={{
                          padding: '2px 4px',
                          textAlign: 'right',
                          fontFamily: 'monospace',
                        }}
                      >
                        {price.toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      )}
    </div>
  )
}
