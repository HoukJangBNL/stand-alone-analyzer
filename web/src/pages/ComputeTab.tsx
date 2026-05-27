// web/src/pages/ComputeTab.tsx
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { StepCard } from '@/components/StepCard'
import { SamRunPanel } from '@/components/run/SamRunPanel'
import { UploadModal } from '@/components/upload/UploadModal'

export function ComputeTab() {
  const { projectId, scanId } = useParams<{ projectId: string; scanId?: string }>()
  const pid = projectId || ''
  const sid = scanId ? Number(scanId) : null
  const [showUpload, setShowUpload] = useState(false)

  if (!pid) {
    return <p data-testid="compute-tab-no-project">Select or create a project.</p>
  }

  if (sid === null) {
    return (
      <div data-testid="compute-tab-no-scan">
        <h2>Compute Tab</h2>
        <p style={{ color: '#6b7280' }}>No scans in this project yet. Create one to get started.</p>
        <button data-testid="compute-tab-new-scan" onClick={() => setShowUpload(true)}>
          + New scan
        </button>
        <UploadModal projectId={pid} open={showUpload} onClose={() => setShowUpload(false)} />
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Compute Tab</h2>
        <button data-testid="compute-tab-new-scan" onClick={() => setShowUpload(true)}>
          + New scan
        </button>
      </div>

      <UploadModal projectId={pid} open={showUpload} onClose={() => setShowUpload(false)} />

      <StepCard projectId={pid} scanId={sid} step="thumbnails" stepName="Thumbnails" />
      <StepCard projectId={pid} scanId={sid} step="background" stepName="Background" />
      <SamRunPanel projectId={pid} scanId={sid} />
      <StepCard projectId={pid} scanId={sid} step="domain_stats" stepName="Domain Stats" />
      <StepCard projectId={pid} scanId={sid} step="domain_proximity" stepName="Domain Proximity" />
    </div>
  )
}
