import { useState } from 'react'
import { useStepProgress } from '@/hooks/useStepProgress'

interface StepCardProps {
  projectId: string
  scanId: number
  step: string
  stepName: string
}

export function StepCard({ projectId, scanId, step, stepName }: StepCardProps) {
  const [params] = useState({})
  const { status, pct, message, start, cancel } = useStepProgress(projectId, scanId, step)

  const handleRun = () => start(params)
  // Server step names use snake_case (domain_stats); testids must be kebab-case.
  const stepSlug = step.replace(/_/g, '-')

  return (
    <div
      data-testid={`compute-${stepSlug}-card`}
      style={{ border: '1px solid #ccc', padding: '16px', margin: '8px 0' }}
    >
      <h3>{stepName}</h3>

      <button
        data-testid={`compute-${stepSlug}-run`}
        onClick={handleRun}
        disabled={status === 'running'}
      >
        Run
      </button>

      {status === 'running' && (
        <button
          data-testid={`compute-${stepSlug}-cancel`}
          onClick={cancel}
          style={{ marginLeft: '8px' }}
        >
          Cancel
        </button>
      )}

      {status === 'running' && (
        <div>
          <div data-testid={`compute-${stepSlug}-pct`}>{Math.round(pct * 100)}%</div>
          <div>{message}</div>
        </div>
      )}

      {status === 'done' && <div>✓ Complete</div>}
      {status === 'error' && <div>✗ Error: {message}</div>}
    </div>
  )
}
