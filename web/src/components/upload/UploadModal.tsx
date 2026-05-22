// web/src/components/upload/UploadModal.tsx
import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ScanForm, type ScanFormValues } from './ScanForm'
import { FileDropzone } from './FileDropzone'
import { ProgressList } from './ProgressList'
import { useUploadStore, resetUploadStore } from '@/state/uploadSlice'
import { createScan, finalizeScan } from '@/api/upload'
import { getOrchestrator, resetOrchestrator } from '@/lib/uploadOrchestrator'

interface Props {
  projectId: string
  open: boolean
  onClose(): void
}

export function UploadModal({ projectId, open, onClose }: Props) {
  const qc = useQueryClient()
  const scanId = useUploadStore((s) => s.scanId)
  const setScanId = useUploadStore((s) => s.setScanId)
  const files = useUploadStore((s) => s.files)
  const order = useUploadStore((s) => s.order)
  const [running, setRunning] = useState(false)
  const [expectedCount, setExpectedCount] = useState(1)

  // hard-reset everything when modal opens fresh
  useEffect(() => {
    if (open) {
      resetUploadStore()
      resetOrchestrator()
    }
  }, [open])

  const createScanMut = useMutation({
    mutationFn: (vals: ScanFormValues) => {
      setExpectedCount(vals.image_count)
      return createScan(projectId, vals)
    },
    onSuccess: (res) => {
      setScanId(res.scan_id)
      toast.success(`Scan ${res.scan_id} created — drop files below`)
    },
    onError: (e: unknown) => {
      toast.error((e as { message?: string })?.message ?? 'createScan failed')
    },
  })

  const finalizeMut = useMutation({
    mutationFn: () => finalizeScan(scanId!, undefined),
    onSuccess: () => {
      toast.success('Scan finalized — ready')
      qc.invalidateQueries({ queryKey: ['scans', 'list', projectId] })
      handleClose()
    },
    onError: (e: unknown) => {
      toast.error((e as { message?: string })?.message ?? 'finalize failed')
    },
  })

  const handleClose = () => {
    resetOrchestrator()
    resetUploadStore()
    setRunning(false)
    onClose()
  }

  const startUpload = async () => {
    setRunning(true)
    try {
      await getOrchestrator().runAll()
    } finally {
      setRunning(false)
    }
  }

  const allDone = order.length > 0 && order.every((uid) => files[uid]?.status === 'done')
  const droppedCount = order.length
  const countMatches = scanId !== null && droppedCount === expectedCount

  if (!open) return null

  return (
    <div
      data-testid="upload-modal-overlay"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose()
      }}
    >
      <div
        data-testid="upload-modal"
        style={{
          background: 'white',
          borderRadius: 6,
          padding: 16,
          width: 720,
          maxWidth: '90vw',
          maxHeight: '90vh',
          overflowY: 'auto',
        }}
      >
        <div
          style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
        >
          <h3 style={{ margin: 0 }}>새 스캔 업로드</h3>
          <button data-testid="upload-modal-close" onClick={handleClose}>
            닫기
          </button>
        </div>

        {!scanId ? (
          <ScanForm
            onSubmit={(v) => createScanMut.mutate(v)}
            disabled={createScanMut.isPending}
          />
        ) : (
          <>
            <p style={{ fontSize: 12, color: '#6b7280' }}>
              scan_id: <code>{scanId}</code> · expected files: {expectedCount}
              {!countMatches && droppedCount > 0 && (
                <span style={{ color: '#b91c1c' }}>
                  {' '}
                  (dropped {droppedCount}, must equal {expectedCount})
                </span>
              )}
            </p>
            <FileDropzone />
            <ProgressList />

            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button
                data-testid="upload-modal-start"
                disabled={running || allDone || !countMatches || droppedCount === 0}
                onClick={startUpload}
              >
                {running ? 'Uploading...' : 'Start upload'}
              </button>
              <button
                data-testid="upload-modal-finalize"
                disabled={!allDone || finalizeMut.isPending}
                onClick={() => finalizeMut.mutate()}
              >
                {finalizeMut.isPending ? 'Finalizing...' : 'Finalize scan'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
