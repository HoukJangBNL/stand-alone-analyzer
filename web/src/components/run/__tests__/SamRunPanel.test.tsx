// web/src/components/run/__tests__/SamRunPanel.test.tsx
/**
 * P3.2 — SamRunPanel renders idle/running/done/error states for the SAM step
 * and forwards weights_path to start(). Per-image messages tagged "ERROR" must
 * be surfaced in red.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SamRunPanel } from '../SamRunPanel'
import { useStepProgress } from '@/hooks/useStepProgress'

vi.mock('@/hooks/useStepProgress')

const mockedUseStepProgress = vi.mocked(useStepProgress)

interface SamResult {
  images: number
  masks_total: number
  errors: number
}

function setHook(overrides: {
  status?: 'idle' | 'running' | 'done' | 'error'
  pct?: number
  message?: string
  result?: SamResult | null
  gpuStatus?: 'idle' | 'launching' | 'ready'
  gpuInstanceId?: string | null
  gpuImageCount?: number | null
  start?: ReturnType<typeof vi.fn>
  cancel?: ReturnType<typeof vi.fn>
}) {
  const start = overrides.start ?? vi.fn()
  const cancel = overrides.cancel ?? vi.fn()
  mockedUseStepProgress.mockReturnValue({
    status: overrides.status ?? 'idle',
    pct: overrides.pct ?? 0,
    message: overrides.message ?? '',
    result: overrides.result ?? null,
    gpuStatus: overrides.gpuStatus ?? 'idle',
    gpuInstanceId: overrides.gpuInstanceId ?? null,
    gpuImageCount: overrides.gpuImageCount ?? null,
    start,
    cancel,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any)
  return { start, cancel }
}

beforeEach(() => {
  mockedUseStepProgress.mockReset()
})

describe('SamRunPanel', () => {
  it('idle: renders Run SAM button enabled', () => {
    setHook({ status: 'idle' })
    render(<SamRunPanel projectId="p1" scanId={42} />)

    const runBtn = screen.getByTestId('compute-sam-run') as HTMLButtonElement
    expect(runBtn).not.toBeNull()
    expect(runBtn.disabled).toBe(false)
    // hook wired with the correct (projectId, scanId, step) tuple
    expect(mockedUseStepProgress).toHaveBeenCalledWith('p1', 42, 'sam')
  })

  it('idle: clicking Run calls start with empty payload (no weights_path)', () => {
    const { start } = setHook({ status: 'idle' })
    render(<SamRunPanel projectId="p1" scanId={42} />)

    fireEvent.click(screen.getByTestId('compute-sam-run'))
    expect(start).toHaveBeenCalledTimes(1)
    expect(start).toHaveBeenCalledWith({})
  })

  it('running: shows progress bar with pct value, message, run disabled, cancel visible', () => {
    setHook({
      status: 'running',
      pct: 0.42,
      message: '[3/7] img_003.tif: 12 masks',
    })
    render(<SamRunPanel projectId="p1" scanId={42} />)

    const runBtn = screen.getByTestId('compute-sam-run') as HTMLButtonElement
    expect(runBtn.disabled).toBe(true)

    const bar = screen.getByTestId('compute-sam-pct') as HTMLProgressElement
    expect(bar.value).toBeCloseTo(0.42)
    expect(bar.max).toBeCloseTo(1)

    const msg = screen.getByTestId('compute-sam-msg')
    expect(msg.textContent).toContain('[3/7] img_003.tif: 12 masks')
    // No ERROR token → not red
    expect(msg.style.color).not.toBe('red')

    expect(screen.getByTestId('compute-sam-cancel')).not.toBeNull()
  })

  it('done: renders Complete + summary stats from result', () => {
    setHook({
      status: 'done',
      pct: 1,
      result: { images: 7, masks_total: 84, errors: 0 },
    })
    render(<SamRunPanel projectId="p1" scanId={42} />)

    const summary = screen.getByTestId('compute-sam-summary')
    expect(summary).not.toBeNull()
    expect(summary.textContent).toContain('7')
    expect(summary.textContent).toContain('84')
    expect(summary.textContent).toContain('0')
    expect(screen.getByText(/complete/i)).not.toBeNull()
  })

  it('error: renders red error message', () => {
    setHook({ status: 'error', message: 'weights missing' })
    render(<SamRunPanel projectId="p1" scanId={42} />)

    const msg = screen.getByTestId('compute-sam-msg')
    expect(msg.textContent).toContain('weights missing')
    expect(msg.style.color).toBe('red')
  })

  it('running with ERROR token in per-image message: message div is red', () => {
    setHook({
      status: 'running',
      pct: 0.5,
      message: '[2/4] img_002.tif: ERROR torch.cuda OOM',
    })
    render(<SamRunPanel projectId="p1" scanId={42} />)

    const msg = screen.getByTestId('compute-sam-msg')
    expect(msg.style.color).toBe('red')
  })
})

describe('SamRunPanel cold-start badges', () => {
  // Task 4 (gpu-dispatcher): SamRunPanel renders two badges as the GPU comes
  // online: gpu_launching → "Launching GPU instance (i-…)…", then gpu_ready →
  // "GPU ready, processing N images". These show before per-image progress.
  it('renders Launching badge when gpuStatus is "launching"', () => {
    setHook({
      status: 'running',
      gpuStatus: 'launching',
      gpuInstanceId: 'i-abc123',
    })
    render(<SamRunPanel projectId="p1" scanId={42} />)

    const badge = screen.getByTestId('sam-progress-gpu-launching')
    expect(badge).not.toBeNull()
    expect(badge.textContent).toMatch(/launching gpu instance.*i-abc123/i)
  })

  it('renders Ready badge when gpuStatus is "ready"', () => {
    setHook({
      status: 'running',
      gpuStatus: 'ready',
      gpuInstanceId: 'i-abc123',
      gpuImageCount: 100,
    })
    render(<SamRunPanel projectId="p1" scanId={42} />)

    const badge = screen.getByTestId('sam-progress-gpu-ready')
    expect(badge).not.toBeNull()
    expect(badge.textContent).toMatch(/gpu ready.*processing 100 images/i)
  })

  it('does not render either badge when gpuStatus is "idle"', () => {
    setHook({ status: 'idle', gpuStatus: 'idle' })
    render(<SamRunPanel projectId="p1" scanId={42} />)

    expect(screen.queryByTestId('sam-progress-gpu-launching')).toBeNull()
    expect(screen.queryByTestId('sam-progress-gpu-ready')).toBeNull()
  })
})
