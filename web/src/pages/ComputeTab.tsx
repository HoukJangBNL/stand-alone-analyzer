// web/src/pages/ComputeTab.tsx
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { StepCard } from '@/components/StepCard'
import { UploadModal } from '@/components/upload/UploadModal'

export function ComputeTab() {
  const { projectId, scanId } = useParams<{ projectId: string; scanId?: string }>()
  const pid = projectId || ''
  const sid = scanId ? Number(scanId) : null
  const [showUpload, setShowUpload] = useState(false)

  if (!pid) {
    return <p data-testid="compute-tab-no-project">프로젝트를 선택하거나 만들어주세요.</p>
  }

  if (sid === null) {
    return (
      <div data-testid="compute-tab-no-scan">
        <h2>Compute Tab</h2>
        <p style={{ color: '#6b7280' }}>이 프로젝트에는 아직 스캔이 없습니다. 새 스캔을 만들어 시작하세요.</p>
        <button data-testid="compute-tab-new-scan" onClick={() => setShowUpload(true)}>
          + 새 스캔
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
          + 새 스캔
        </button>
      </div>

      <UploadModal projectId={pid} open={showUpload} onClose={() => setShowUpload(false)} />

      <StepCard projectId={pid} scanId={sid} step="thumbnails" stepName="Thumbnails" />
      <StepCard projectId={pid} scanId={sid} step="background" stepName="Background" />
      <StepCard projectId={pid} scanId={sid} step="domain_stats" stepName="Domain Stats" />
      <StepCard projectId={pid} scanId={sid} step="domain_proximity" stepName="Domain Proximity" />
    </div>
  )
}
