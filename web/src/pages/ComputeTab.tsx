import { useParams } from 'react-router-dom'
import { StepCard } from '@/components/StepCard'

export function ComputeTab() {
  const { projectId } = useParams<{ projectId: string }>()

  return (
    <div>
      <h2>Compute Tab</h2>

      <StepCard
        projectId={projectId || 'local'}
        step="thumbnails"
        stepName="Thumbnails"
      />

      <StepCard
        projectId={projectId || 'local'}
        step="background"
        stepName="Background"
      />

      <StepCard
        projectId={projectId || 'local'}
        step="domain_stats"
        stepName="Domain Stats"
      />

      <StepCard
        projectId={projectId || 'local'}
        step="domain_proximity"
        stepName="Domain Proximity"
      />
    </div>
  )
}
