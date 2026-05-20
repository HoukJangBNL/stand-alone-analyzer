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
    render(<StepCard projectId="local" step="thumbnails" stepName="Thumbnails" />)

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

    render(<StepCard projectId="local" step="thumbnails" stepName="Thumbnails" />)

    expect(screen.getByText(/halfway/i)).not.toBeNull()
  })
})
