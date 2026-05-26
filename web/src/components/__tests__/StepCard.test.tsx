import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StepCard } from '../StepCard'
import { useStepProgress } from '@/hooks/useStepProgress'

vi.mock('@/hooks/useStepProgress')

const mockedUseStepProgress = vi.mocked(useStepProgress)

beforeEach(() => {
  // default: idle
  mockedUseStepProgress.mockReturnValue({
    status: 'idle',
    pct: 0,
    message: '',
    start: vi.fn(),
    cancel: vi.fn(),
  } as any)
})

describe('StepCard', () => {
  it('renders step name and run button', () => {
    render(<StepCard projectId="local" scanId={1} step="thumbnails" stepName="Thumbnails" />)

    expect(screen.getByText(/thumbnails/i)).not.toBeNull()
    expect(screen.getByRole('button', { name: /run/i })).not.toBeNull()
  })

  it('shows progress bar when running', () => {
    mockedUseStepProgress.mockReturnValue({
      status: 'running',
      pct: 0.5,
      message: 'halfway',
      start: vi.fn(),
      cancel: vi.fn(),
    } as any)

    render(<StepCard projectId="local" scanId={1} step="thumbnails" stepName="Thumbnails" />)

    expect(screen.getByText(/halfway/i)).not.toBeNull()
  })

  it('forwards scanId to useStepProgress (Task C1)', () => {
    render(
      <StepCard
        projectId="p1"
        scanId={42}
        step="thumbnails"
        stepName="Thumbnails"
      />
    )
    // Hook must be called with (projectId, scanId, step). Without this the
    // run URL drops the /scans/{sid}/ segment and 404s.
    expect(mockedUseStepProgress).toHaveBeenCalledWith('p1', 42, 'thumbnails')
  })
})
