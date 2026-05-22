import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { StepCard } from '@/components/StepCard'
import { UploadModal } from '@/components/upload/UploadModal'

export function ComputeTab() {
  const { projectId } = useParams<{ projectId: string }>()
  const pid = projectId || 'local'
  const [showUpload, setShowUpload] = useState(false)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Compute Tab</h2>
        <button data-testid="compute-tab-new-scan" onClick={() => setShowUpload(true)}>
          + 새 스캔
        </button>
      </div>

      <UploadModal projectId={pid} open={showUpload} onClose={() => setShowUpload(false)} />

      <StepCard projectId={pid} step="thumbnails" stepName="Thumbnails" />
      <StepCard projectId={pid} step="background" stepName="Background" />
      <StepCard projectId={pid} step="domain_stats" stepName="Domain Stats" />
      <StepCard projectId={pid} step="domain_proximity" stepName="Domain Proximity" />
    </div>
  )
}
