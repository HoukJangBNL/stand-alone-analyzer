// web/src/components/upload/UploadModal.tsx
import { useEffect, useMemo, useRef, useState } from 'react'
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

/**
 * UploadModal — new-scan-only.
 *
 * Every open creates a fresh scan via createScan(). The modal never resumes,
 * appends to, or otherwise targets a pre-existing scan. The hard reset in the
 * `open` effect (resetUploadStore / resetOrchestrator / setScanMeta) enforces
 * this client-side.
 *
 * This invariant is load-bearing: the W11 backend guards
 * (require_editor_for_scan, get_project_for_user) and the wrong-project 404
 * contract in presign_image_put / complete_image / finalize_scan assume each
 * upload session corresponds to a scan the current user just created. Adding
 * a "resume scan" path would require revisiting those guards and the ACL /
 * re-validation flows that don't exist on the client yet — see
 * docs/superpowers/plans/2026-05-26-W11-scan-guards.md before changing this.
 */
export function UploadModal({ projectId, open, onClose }: Props) {
  const qc = useQueryClient()
  const scanId = useUploadStore((s) => s.scanId)
  const setScanId = useUploadStore((s) => s.setScanId)
  const files = useUploadStore((s) => s.files)
  const order = useUploadStore((s) => s.order)
  const [running, setRunning] = useState(false)
  // When the user clicks Close while an upload is running, we want a soft
  // confirmation step instead of nuking client + server state. The inline
  // confirm panel is rendered when this flag is true.
  const [confirmingClose, setConfirmingClose] = useState(false)
  // Lifted-up form state — UploadModal is the source of truth for scan
  // metadata while the user is filling it in. Start upload reads this
  // directly, so there's no separate "Save" gate.
  const [scanMeta, setScanMeta] = useState<ScanFormValues>(EMPTY_META)
  // AbortController for the in-flight createScan request. Stop & Close
  // aborts this so a stale onSuccess can't write scanId / fire a toast on a
  // closed modal. Owned by a ref because we need to .abort() across renders
  // without retriggering effects.
  const createScanCtrlRef = useRef<AbortController | null>(null)

  // hard-reset everything when modal opens fresh
  useEffect(() => {
    if (open) {
      resetUploadStore()
      resetOrchestrator()
      setScanMeta(EMPTY_META)
      setConfirmingClose(false)
      createScanCtrlRef.current = null
    }
  }, [open])

  const createScanMut = useMutation({
    mutationFn: (vals: ScanFormValues & { image_count: number }) => {
      const ctrl = new AbortController()
      createScanCtrlRef.current = ctrl
      return createScan(projectId, vals, ctrl.signal)
    },
    onSuccess: (res) => {
      // Defensive: if the user already closed the modal mid-flight, the
      // controller will be aborted. Skip the side effects so we don't leak
      // scanId or pop a toast on a closed modal.
      if (createScanCtrlRef.current?.signal.aborted) return
      setScanId(res.scan_id)
      toast.success(`Scan ${res.scan_id} created`)
    },
    onError: (e: unknown) => {
      // AbortError on a deliberate Stop & Close is not a user-facing error.
      const name = (e as { name?: string })?.name
      if (name === 'AbortError' || createScanCtrlRef.current?.signal.aborted) return
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
    // Mid-upload close goes through a confirm step so we don't lose the
    // server-side scan and the user's in-flight work without warning. The
    // confirm panel decides which reset path runs.
    if (running) {
      setConfirmingClose(true)
      return
    }
    resetOrchestrator()
    resetUploadStore()
    setRunning(false)
    setScanMeta(EMPTY_META)
    onClose()
  }

  // User confirmed they want to abandon the in-flight upload. Abort fetches
  // and drop only the throwaway client-side rows (queued / uploading /
  // failed). `scanId` and `done` rows survive so the server-side scan stays
  // intact — a future task can wire up a resume UX.
  const handleConfirmStopAndClose = () => {
    // Abort the createScan mutation FIRST so a late-arriving response can't
    // race past clearTransientFiles and write scanId / fire a toast on a
    // closed modal. The abort flag is also what onSuccess/onError check.
    createScanCtrlRef.current?.abort()
    getOrchestrator().cancelAll()
    useUploadStore.getState().clearTransientFiles()
    setRunning(false)
    setConfirmingClose(false)
    setScanMeta(EMPTY_META)
    onClose()
  }

  const handleCancelClose = () => {
    setConfirmingClose(false)
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
      // Bail if Stop & Close fired between createScan and runAll: the
      // controller is aborted, scanId may have been intentionally left out
      // (when onSuccess no-ops on aborted), and runAll would throw on a
      // missing scanId. This keeps the cancel path silent.
      if (createScanCtrlRef.current?.signal.aborted) return
      await getOrchestrator().runAll()
    } finally {
      setRunning(false)
    }
  }

  const allDone = order.length > 0 && order.every((uid) => files[uid]?.status === 'done')
  const droppedCount = order.length

  // Batch retry: flip every failed row back to queued and re-run. Mirrors
  // startUpload's running-flag wrapping so the rest of the modal (Close
  // confirm, Start disabled, retry-all hidden) reacts correctly while the
  // batch is in flight.
  const retryAllFailed = async () => {
    setRunning(true)
    try {
      await getOrchestrator().retryAllFailed()
    } finally {
      setRunning(false)
    }
  }

  // Aggregate counts for the modal-level progress summary. With 3000+ files
  // the per-row list is virtualized/truncated, so users need a single
  // headline number to know how the batch is going.
  const counts = useMemo(() => {
    let done = 0
    let uploading = 0
    let failed = 0
    let queued = 0
    for (const uid of order) {
      const st = files[uid]?.status
      if (st === 'done') done++
      else if (st === 'uploading') uploading++
      else if (st === 'failed') failed++
      else queued++
    }
    return { done, uploading, failed, queued }
  }, [order, files])

  // Surface why Start is disabled — for 4000-file folders the ScanForm
  // scrolls out of view and users can't tell what's missing.
  const startBlockReasons: string[] = []
  if (!scanMeta.name.trim()) startBlockReasons.push('scan name')
  if (!scanMeta.material.trim()) startBlockReasons.push('material')
  if (droppedCount === 0) startBlockReasons.push('at least one file')

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
        // Ignore backdrop clicks while the confirm overlay is up — clicking
        // through the dimmed area shouldn't re-trigger handleClose; the user
        // must use the explicit Cancel / Stop & Close buttons.
        if (e.target === e.currentTarget && !confirmingClose) handleClose()
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
          position: 'relative',
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

        <div
          style={{
            position: 'sticky',
            bottom: 0,
            background: 'white',
            paddingTop: 8,
            marginTop: 12,
            borderTop: '1px solid #e5e7eb',
          }}
        >
          {order.length > 0 && (
            <p
              data-testid="upload-modal-counts"
              style={{ margin: '0 0 8px', fontSize: 13, color: '#374151' }}
            >
              {counts.done} done · {counts.uploading} uploading · {counts.failed} failed ·{' '}
              {counts.queued} queued of {order.length}
            </p>
          )}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              data-testid="upload-modal-start"
              disabled={
                running ||
                allDone ||
                !metaValid ||
                droppedCount === 0 ||
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
            {counts.failed > 0 && !running && (
              <button data-testid="upload-modal-retry-all" onClick={retryAllFailed}>
                Retry failed ({counts.failed})
              </button>
            )}
            {!running && !allDone && startBlockReasons.length > 0 && (
              <span
                data-testid="upload-modal-start-blocked-reason"
                style={{ color: '#b45309', fontSize: 12 }}
              >
                Need: {startBlockReasons.join(', ')}.
              </span>
            )}
          </div>
        </div>

        {confirmingClose && (
          <div
            data-testid="upload-modal-close-confirm"
            // Inline modal-within-modal. We deliberately don't use
            // window.confirm — jsdom can't drive the native dialog and the
            // app needs custom button labels anyway.
            style={{
              position: 'absolute',
              inset: 0,
              background: 'rgba(0,0,0,0.35)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <div
              style={{
                background: 'white',
                borderRadius: 6,
                padding: 16,
                width: 360,
                maxWidth: '80%',
                boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
              }}
            >
              <p style={{ margin: '0 0 12px', fontSize: 14 }}>
                Upload is still running. Close anyway? Already-uploaded files
                are kept on the server.
              </p>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                <button
                  data-testid="upload-modal-close-confirm-cancel"
                  onClick={handleCancelClose}
                >
                  Cancel
                </button>
                <button
                  data-testid="upload-modal-close-confirm-stop"
                  onClick={handleConfirmStopAndClose}
                >
                  Stop &amp; Close
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
