import { useState } from 'react'
import { useStepProgress } from '@/hooks/useStepProgress'

interface StepCardProps {
  projectId: string
  step: string
  stepName: string
}

export function StepCard({ projectId, step, stepName }: StepCardProps) {
  const [params] = useState({})
  const { status, pct, message, start, cancel } = useStepProgress(projectId, step)

  const handleRun = () => start(params)

  return (
    <div
      data-testid={`compute-${step}-card`}
      style={{ border: '1px solid #ccc', padding: '16px', margin: '8px 0' }}
    >
      <h3>{stepName}</h3>

      <button
        data-testid={`compute-${step}-run`}
        onClick={handleRun}
        disabled={status === 'running'}
      >
        Run
      </button>

      {status === 'running' && (
        <button
          data-testid={`compute-${step}-cancel`}
          onClick={cancel}
          style={{ marginLeft: '8px' }}
        >
          Cancel
        </button>
      )}

      {status === 'running' && (
        <div>
          <div data-testid={`compute-${step}-pct`}>{Math.round(pct * 100)}%</div>
          <div>{message}</div>
        </div>
      )}

      {status === 'done' && <div>✓ Complete</div>}
      {status === 'error' && <div>✗ Error: {message}</div>}
    </div>
  )
}
