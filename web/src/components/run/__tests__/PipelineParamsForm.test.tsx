// web/src/components/run/__tests__/PipelineParamsForm.test.tsx
/**
 * P5.3 — PipelineParamsForm exposes the 5-step PipelineBody and gates the run
 * button on the SAM weights_path. Background dirty signal feeds the cascade
 * confirmation flow added in P5.4.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PipelineParamsForm } from '../PipelineParamsForm'

describe('PipelineParamsForm', () => {
  it('renders all five collapsible step sections', () => {
    render(<PipelineParamsForm onSubmit={() => {}} />)
    expect(screen.getByTestId('pipeline-form')).toBeTruthy()
    expect(screen.getByTestId('pipeline-form-section-thumbnails')).toBeTruthy()
    expect(screen.getByTestId('pipeline-form-section-background')).toBeTruthy()
    expect(screen.getByTestId('pipeline-form-section-sam')).toBeTruthy()
    expect(screen.getByTestId('pipeline-form-section-domain-stats')).toBeTruthy()
    expect(
      screen.getByTestId('pipeline-form-section-domain-proximity')
    ).toBeTruthy()
  })

  it('Run button is enabled without weights_path (no longer gated)', () => {
    render(
      <PipelineParamsForm
        initialValues={{}}
        onSubmit={() => {}}
      />
    )
    const runBtn = screen.getByTestId('pipeline-form-run') as HTMLButtonElement
    expect(runBtn.disabled).toBe(false)
  })

  it('clicking Run calls onSubmit with a complete PipelineBody (sam without weights_path)', () => {
    const onSubmit = vi.fn()
    render(
      <PipelineParamsForm
        initialValues={{}}
        onSubmit={onSubmit}
      />
    )
    fireEvent.click(screen.getByTestId('pipeline-form-run'))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    const body = onSubmit.mock.calls[0][0]
    expect(body).toHaveProperty('thumbnails')
    expect(body).toHaveProperty('background')
    expect(body).toHaveProperty('sam')
    expect(body).toHaveProperty('domain_stats')
    expect(body).toHaveProperty('domain_proximity')
    // sam should not have weights_path
    expect(body.sam).not.toHaveProperty('weights_path')
  })

  it('changing background.seed fires onBackgroundDirty(true), reverting fires (false)', () => {
    const onBackgroundDirty = vi.fn()
    render(
      <PipelineParamsForm
        initialValues={{
          background: {
            seed: 0,
            max_images: 100,
            gaussian_sigma: 10.0,
            method: 'median',
          },
        }}
        onSubmit={() => {}}
        onBackgroundDirty={onBackgroundDirty}
      />
    )
    const seedInput = screen.getByTestId(
      'pipeline-form-background-seed'
    ) as HTMLInputElement
    fireEvent.change(seedInput, { target: { value: '42' } })
    expect(onBackgroundDirty).toHaveBeenCalledWith(true)

    fireEvent.change(seedInput, { target: { value: '0' } })
    // Last call: revert to clean
    const lastCall =
      onBackgroundDirty.mock.calls[onBackgroundDirty.mock.calls.length - 1]
    expect(lastCall[0]).toBe(false)
  })

  it('isRunning=true disables the Run button', () => {
    render(
      <PipelineParamsForm
        initialValues={{}}
        onSubmit={() => {}}
        isRunning
      />
    )
    const runBtn = screen.getByTestId('pipeline-form-run') as HTMLButtonElement
    expect(runBtn.disabled).toBe(true)
  })
})
