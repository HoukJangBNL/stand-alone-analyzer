// web/src/pages/__tests__/ComputeTab.pipeline.test.tsx
/**
 * P5.4 — ComputeTab integration with the pipeline UX:
 *  - Idle render shows the 5-row PipelineTimeline.
 *  - Submitting with a clean Background section calls start() directly.
 *  - Submitting with a dirty Background section opens the cascade dialog;
 *    confirm calls start(); cancel does not.
 *
 * The hook is mocked so we can assert the call count + arguments to start()
 * without touching transport.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import type { PipelineState } from '@/hooks/usePipelineProgress'
import { ComputeTab } from '@/pages/ComputeTab'

// Shared mocks for the hook. Tests assign fresh fns in beforeEach.
const startMock = vi.fn()
const cancelMock = vi.fn()

const idleState: PipelineState = {
  phase: 'idle',
  steps: {
    thumbnails: { status: 'idle', pct: 0, message: '', result: null },
    background: { status: 'idle', pct: 0, message: '', result: null },
    sam: { status: 'idle', pct: 0, message: '', result: null },
    domain_stats: { status: 'idle', pct: 0, message: '', result: null },
    domain_proximity: { status: 'idle', pct: 0, message: '', result: null },
  },
  currentStep: null,
  error: null,
  cascade: null,
}

vi.mock('@/hooks/usePipelineProgress', async () => {
  // Re-export the type-only surface untouched, override the hook impl.
  const actual = await vi.importActual<
    typeof import('@/hooks/usePipelineProgress')
  >('@/hooks/usePipelineProgress')
  return {
    ...actual,
    usePipelineProgress: () => ({
      state: idleState,
      start: startMock,
      cancel: cancelMock,
    }),
  }
})

// SamRunPanel is irrelevant to this suite and pulls in fetch-driven state;
// stub it to a noop so it never flakes the assertions.
vi.mock('@/components/run/SamRunPanel', () => ({
  SamRunPanel: () => <div data-testid="sam-run-panel-stub" />,
}))

// UploadModal renders a portal-style overlay we don't need here.
vi.mock('@/components/upload/UploadModal', () => ({
  UploadModal: () => null,
}))

function renderTab() {
  return render(
    <MemoryRouter initialEntries={['/projects/local/scans/11/compute']}>
      <Routes>
        <Route
          path="/projects/:projectId/scans/:scanId/compute"
          element={<ComputeTab />}
        />
      </Routes>
    </MemoryRouter>
  )
}

describe('ComputeTab pipeline integration (P5.4)', () => {
  beforeEach(() => {
    startMock.mockReset()
    cancelMock.mockReset()
  })

  it('renders the 5-row timeline in idle state on mount', () => {
    renderTab()
    const timeline = screen.getByTestId('pipeline-timeline')
    expect(timeline).toBeTruthy()
    // Each row exists with idle status. Pretty names are inside the rows;
    // we scope by row testid since the form's <summary> tags also render the
    // same labels above the timeline.
    expect(screen.getByTestId('pipeline-timeline-row-thumbnails')).toBeTruthy()
    expect(screen.getByTestId('pipeline-timeline-row-background')).toBeTruthy()
    expect(screen.getByTestId('pipeline-timeline-row-sam')).toBeTruthy()
    expect(screen.getByTestId('pipeline-timeline-row-domain-stats')).toBeTruthy()
    expect(
      screen.getByTestId('pipeline-timeline-row-domain-proximity')
    ).toBeTruthy()
    // Sanity: the rendered timeline contains all five pretty names.
    expect(timeline.textContent).toContain('Thumbnails')
    expect(timeline.textContent).toContain('Background')
    expect(timeline.textContent).toContain('SAM')
    expect(timeline.textContent).toContain('Domain Stats')
    expect(timeline.textContent).toContain('Domain Proximity')
  })

  it('clicking Run with clean Background calls start() once and skips the dialog', () => {
    renderTab()
    fireEvent.click(screen.getByTestId('pipeline-form-run'))
    expect(startMock).toHaveBeenCalledTimes(1)
    const body = startMock.mock.calls[0][0]
    expect(body).toHaveProperty('thumbnails')
    expect(body).toHaveProperty('background')
    expect(body).toHaveProperty('sam')
    expect(body).toHaveProperty('domain_stats')
    expect(body).toHaveProperty('domain_proximity')
    expect(screen.queryByTestId('cascade-confirm-dialog')).toBeNull()
  })

  it('clicking Run with dirty Background opens the cascade dialog and defers start()', () => {
    renderTab()
    // Dirty the background section by changing seed away from the default (0).
    const seedInput = screen.getByTestId(
      'pipeline-form-background-seed'
    ) as HTMLInputElement
    fireEvent.change(seedInput, { target: { value: '42' } })

    fireEvent.click(screen.getByTestId('pipeline-form-run'))

    const dialog = screen.getByTestId('cascade-confirm-dialog')
    expect(dialog).toBeTruthy()
    expect(dialog.textContent).toContain(
      'Background parameters changed. This will rerun: SAM, Domain Stats, Domain Proximity. Continue?'
    )
    expect(startMock).not.toHaveBeenCalled()
  })

  it('confirming the cascade dialog calls start() and dismisses the dialog', () => {
    renderTab()
    const seedInput = screen.getByTestId(
      'pipeline-form-background-seed'
    ) as HTMLInputElement
    fireEvent.change(seedInput, { target: { value: '42' } })
    fireEvent.click(screen.getByTestId('pipeline-form-run'))

    fireEvent.click(screen.getByTestId('cascade-confirm'))

    expect(startMock).toHaveBeenCalledTimes(1)
    const body = startMock.mock.calls[0][0]
    expect(body.background.seed).toBe(42)
    expect(screen.queryByTestId('cascade-confirm-dialog')).toBeNull()
  })

  it('cancelling the cascade dialog does not call start() and dismisses the dialog', () => {
    renderTab()
    const seedInput = screen.getByTestId(
      'pipeline-form-background-seed'
    ) as HTMLInputElement
    fireEvent.change(seedInput, { target: { value: '42' } })
    fireEvent.click(screen.getByTestId('pipeline-form-run'))

    fireEvent.click(screen.getByTestId('cascade-cancel'))

    expect(startMock).not.toHaveBeenCalled()
    expect(screen.queryByTestId('cascade-confirm-dialog')).toBeNull()
  })
})
