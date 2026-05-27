// web/src/components/run/__tests__/PipelineTimeline.test.tsx
/**
 * P5.3 — PipelineTimeline renders five rows in execution order driven by
 * PipelineState. Status icon, progress bar, and message reflect each step.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PipelineTimeline } from '../PipelineTimeline'
import type { PipelineState } from '@/hooks/usePipelineProgress'

const STEPS = [
  'thumbnails',
  'background',
  'sam',
  'domain_stats',
  'domain_proximity',
] as const

function freshState(): PipelineState {
  const steps = {} as PipelineState['steps']
  for (const step of STEPS) {
    steps[step] = { status: 'idle', pct: 0, message: '', result: null }
  }
  return {
    phase: 'idle',
    steps,
    currentStep: null,
    error: null,
    cascade: null,
  }
}

describe('PipelineTimeline', () => {
  it('idle state: renders 5 rows, all status idle', () => {
    render(<PipelineTimeline state={freshState()} />)
    expect(screen.getByTestId('pipeline-timeline')).toBeTruthy()
    for (const step of STEPS) {
      const slug = step.replace(/_/g, '-')
      const row = screen.getByTestId(`pipeline-timeline-row-${slug}`)
      expect(row).toBeTruthy()
      const status = screen.getByTestId(`pipeline-timeline-row-${slug}-status`)
      expect(status.getAttribute('data-status')).toBe('idle')
    }
  })

  it('running mid-pipeline: thumbnails done, background running with pct', () => {
    const state = freshState()
    state.phase = 'running'
    state.currentStep = 'background'
    state.steps.thumbnails = {
      status: 'done',
      pct: 1,
      message: '',
      result: {},
    }
    state.steps.background = {
      status: 'running',
      pct: 0.42,
      message: 'fitting median',
      result: null,
    }

    render(<PipelineTimeline state={state} />)
    expect(
      screen.getByTestId('pipeline-timeline-row-thumbnails-status').getAttribute(
        'data-status'
      )
    ).toBe('done')
    expect(
      screen.getByTestId('pipeline-timeline-row-background-status').getAttribute(
        'data-status'
      )
    ).toBe('running')
    expect(
      screen.getByTestId('pipeline-timeline-row-background-msg').textContent
    ).toContain('fitting median')
    const pct = screen.getByTestId('pipeline-timeline-row-background-pct')
    expect(pct.textContent).toContain('42')
  })

  it('terminal done: all 5 rows show done', () => {
    const state = freshState()
    state.phase = 'done'
    for (const step of STEPS) {
      state.steps[step] = { status: 'done', pct: 1, message: '', result: {} }
    }

    render(<PipelineTimeline state={state} />)
    for (const step of STEPS) {
      const slug = step.replace(/_/g, '-')
      expect(
        screen
          .getByTestId(`pipeline-timeline-row-${slug}-status`)
          .getAttribute('data-status')
      ).toBe('done')
    }
  })

  it('terminal error on sam: sam row shows error with the message', () => {
    const state = freshState()
    state.phase = 'error'
    state.steps.thumbnails = {
      status: 'done',
      pct: 1,
      message: '',
      result: {},
    }
    state.steps.background = {
      status: 'done',
      pct: 1,
      message: '',
      result: {},
    }
    state.steps.sam = {
      status: 'error',
      pct: 0.3,
      message: 'weights missing',
      result: null,
    }
    state.error = {
      // Shape mirrors ApiError-ish; component only reads steps[step].message.
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any

    render(<PipelineTimeline state={state} />)
    expect(
      screen
        .getByTestId('pipeline-timeline-row-sam-status')
        .getAttribute('data-status')
    ).toBe('error')
    expect(
      screen.getByTestId('pipeline-timeline-row-sam-msg').textContent
    ).toContain('weights missing')
  })
})
