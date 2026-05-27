// web/src/pages/ComputeTab.tsx
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { SamRunPanel } from '@/components/run/SamRunPanel'
import { UploadModal } from '@/components/upload/UploadModal'
import { PipelineParamsForm } from '@/components/run/PipelineParamsForm'
import { PipelineTimeline } from '@/components/run/PipelineTimeline'
import { CascadeConfirmDialog } from '@/components/run/CascadeConfirmDialog'
import {
  usePipelineProgress,
  type PipelineBody,
} from '@/hooks/usePipelineProgress'

export function ComputeTab() {
  const { projectId, scanId } = useParams<{ projectId: string; scanId?: string }>()
  const pid = projectId || ''
  const sid = scanId ? Number(scanId) : null
  const [showUpload, setShowUpload] = useState(false)
  const [bgDirty, setBgDirty] = useState(false)
  const [pendingBody, setPendingBody] = useState<PipelineBody | null>(null)

  // Hook order must be stable across renders; pass a placeholder scanId when
  // sid is missing — start() is only reachable from the sid-present branch
  // below, so the placeholder is never used at runtime.
  const { state: pipelineState, start } = usePipelineProgress(pid, sid ?? 0)

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

  const handleRun = (body: PipelineBody) => {
    if (bgDirty) {
      setPendingBody(body)
    } else {
      void start(body)
    }
  }

  const handleConfirmCascade = () => {
    if (pendingBody) {
      void start(pendingBody)
    }
    setPendingBody(null)
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

      <section style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <PipelineParamsForm
          onSubmit={handleRun}
          onBackgroundDirty={setBgDirty}
          isRunning={pipelineState.phase === 'running'}
        />
        <PipelineTimeline state={pipelineState} />
      </section>

      <section style={{ marginTop: 24 }}>
        <h3 style={{ margin: '0 0 4px 0', fontSize: '0.95em' }}>Single-step fallback</h3>
        <p style={{ margin: '0 0 8px 0', color: '#6b7280', fontSize: '0.85em' }}>
          Run SAM on its own without the rest of the pipeline.
        </p>
        <SamRunPanel projectId={pid} scanId={sid} />
      </section>

      {pendingBody !== null && (
        <CascadeConfirmDialog
          onCancel={() => setPendingBody(null)}
          onConfirm={handleConfirmCascade}
        />
      )}
    </div>
  )
}
