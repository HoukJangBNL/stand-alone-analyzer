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

const EMPTY_META: ScanFormValues = { name: '', material: '', extra_metadata: {} }

export function UploadModal({ projectId, open, onClose }: Props) {
  const qc = useQueryClient()
  const scanId = useUploadStore((s) => s.scanId)
  const setScanId = useUploadStore((s) => s.setScanId)
  const files = useUploadStore((s) => s.files)
  const order = useUploadStore((s) => s.order)
  const [running, setRunning] = useState(false)
  // Lifted-up form state — UploadModal is the source of truth for scan
  // metadata while the user is filling it in. Start upload reads this
  // directly, so there's no separate "Save" gate.
  const [scanMeta, setScanMeta] = useState<ScanFormValues>(EMPTY_META)

  // hard-reset everything when modal opens fresh
  useEffect(() => {
    if (open) {
      resetUploadStore()
      resetOrchestrator()
      setScanMeta(EMPTY_META)
    }
  }, [open])

  const createScanMut = useMutation({
    mutationFn: (vals: ScanFormValues & { image_count: number }) => createScan(projectId, vals),
    onSuccess: (res) => {
      setScanId(res.scan_id)
      toast.success(`Scan ${res.scan_id} created`)
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
    setScanMeta(EMPTY_META)
    onClose()
  }

  const metaValid = scanMeta.name.trim().length > 0 && scanMeta.material.trim().length > 0

  const startUpload = async () => {
    if (!metaValid) return
    setRunning(true)
    try {
      // Lazy scan creation: derive image_count from the actual dropped files.
      if (!scanId) {
        await createScanMut.mutateAsync({
          ...scanMeta,
          name: scanMeta.name.trim(),
          image_count: order.length,
        })
      }
      await getOrchestrator().runAll()
    } finally {
      setRunning(false)
    }
  }

  const allDone = order.length > 0 && order.every((uid) => files[uid]?.status === 'done')
  const droppedCount = order.length

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
          <h3 style={{ margin: 0 }}>Upload new scan</h3>
          <button data-testid="upload-modal-close" onClick={handleClose}>
            Close
          </button>
        </div>

        <ScanForm value={scanMeta} onChange={setScanMeta} disabled={running} />

        <p style={{ fontSize: 12, color: '#6b7280' }}>
          {scanId ? (
            <>
              scan_id: <code>{scanId}</code> · files: {droppedCount}
            </>
          ) : (
            <>files: {droppedCount}</>
          )}
        </p>
        <FileDropzone />
        <ProgressList />

        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button
            data-testid="upload-modal-start"
            disabled={
              running ||
              allDone ||
              droppedCount === 0 ||
              !metaValid ||
              createScanMut.isPending
            }
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
      </div>
    </div>
  )
}
