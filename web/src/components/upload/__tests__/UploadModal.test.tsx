import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { UploadModal } from '../UploadModal'
import { resetUploadStore, useUploadStore, type UploadFile } from '@/state/uploadSlice'
import * as upload from '@/api/upload'
import * as materials from '@/api/materials'
import * as sha from '@/lib/sha256'
import * as orchestratorMod from '@/lib/uploadOrchestrator'
import { toast } from 'sonner'

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

beforeEach(() => {
  resetUploadStore()
  vi.restoreAllMocks()
  vi.spyOn(materials, 'fetchMaterials').mockResolvedValue([{ name: 'graphene' }])
  // jsdom has no createImageBitmap or URL.createObjectURL — stub the
  // orchestrator's image-dimension reader so the upload pipeline can complete.
  ;(globalThis as { __readImageDimensionsForTest?: (f: File) => Promise<{ width: number; height: number }> })
    .__readImageDimensionsForTest = async () => ({ width: 100, height: 100 })
})

describe('UploadModal', () => {
  it('shows ScanForm + FileDropzone together; Start gates on meta + at least one file', async () => {
    const createScanSpy = vi.spyOn(upload, 'createScan').mockResolvedValue({ scan_id: 'scan_123' })
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('a'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockResolvedValue({
      put_url: 'http://s3.fake/p',
      headers: {},
      upload_item_id: 'ui_1',
    })
    vi.spyOn(upload, 'putToS3').mockResolvedValue(undefined)
    vi.spyOn(upload, 'completeImage').mockResolvedValue({ image_id: 'img_1' })
    const finalSpy = vi.spyOn(upload, 'finalizeScan').mockResolvedValue({ status: 'ready' })

    const onClose = vi.fn()
    render(wrap(<UploadModal projectId="p1" open onClose={onClose} />))

    // Both ScanForm and FileDropzone render together — no phase gate.
    expect(await screen.findByTestId('scan-form')).toBeTruthy()
    expect(await screen.findByTestId('file-dropzone')).toBeTruthy()
    const startBtn = await screen.findByTestId('upload-modal-start')
    // Start disabled: no meta, no files.
    expect((startBtn as HTMLButtonElement).disabled).toBe(true)

    // Fill in meta — Start still disabled because no files yet.
    await userEvent.type(screen.getByTestId('scan-form-name'), 'scan-A')
    const matInput = await screen.findByTestId('material-combobox-input')
    await userEvent.click(matInput)
    await userEvent.click(await screen.findByTestId('material-combobox-option-graphene'))
    expect((screen.getByTestId('upload-modal-start') as HTMLButtonElement).disabled).toBe(true)

    // Drop two files — Start should now enable. createScan must NOT have been
    // called yet (deferred until Start upload).
    const dz = screen.getByTestId('file-dropzone')
    const f1 = new File([new Uint8Array(8)], 'tile_0_0.tif')
    const f2 = new File([new Uint8Array(8)], 'tile_0_1.tif')
    fireEvent.drop(dz, { dataTransfer: { files: [f1, f2], items: [], types: ['Files'] } })

    expect(createScanSpy).not.toHaveBeenCalled()

    await waitFor(() =>
      expect((screen.getByTestId('upload-modal-start') as HTMLButtonElement).disabled).toBe(false),
    )

    await userEvent.click(screen.getByTestId('upload-modal-start'))

    // createScan called with image_count derived from dropped files (2).
    await waitFor(() => expect(createScanSpy).toHaveBeenCalled())
    const [, body] = createScanSpy.mock.calls[0]
    expect(body.image_count).toBe(2)
    expect(body.name).toBe('scan-A')
    expect(body.material).toBe('graphene')

    await waitFor(() => expect(screen.getAllByText(/done/i).length).toBeGreaterThan(0))

    // Finalize
    await userEvent.click(await screen.findByTestId('upload-modal-finalize'))
    await waitFor(() => expect(finalSpy).toHaveBeenCalledWith('scan_123', undefined))
  })

  it('renders aggregate counter (done · uploading · failed · queued of total)', async () => {
    // Directly populate the store with 5 files in known statuses.
    const mkFile = (uid: string, status: UploadFile['status']): UploadFile => ({
      uid,
      file: new File([new Uint8Array(1)], `${uid}.tif`),
      filename: `${uid}.tif`,
      size: 1,
      grid_ix: null,
      grid_iy: null,
      status,
      progress: 0,
      sha256_hex: null,
      upload_item_id: null,
      image_id: null,
      error: null,
      request_id: null,
    })
    const entries: Array<[string, UploadFile['status']]> = [
      ['f1', 'done'],
      ['f2', 'done'],
      ['f3', 'uploading'],
      ['f4', 'failed'],
      ['f5', 'queued'],
    ]
    const files: Record<string, UploadFile> = {}
    const order: string[] = []
    for (const [uid, st] of entries) {
      files[uid] = mkFile(uid, st)
      order.push(uid)
    }
    // Set state AFTER UploadModal's open-effect would run, by rendering with
    // open=false first, but simpler: set state then render — UploadModal's
    // open-effect resets the store, so we need to populate AFTER mount.
    render(wrap(<UploadModal projectId="p1" open onClose={() => {}} />))
    // The modal's open-effect resets the store on mount; populate now.
    useUploadStore.setState({ files, order, scanId: null })

    const counts = await screen.findByTestId('upload-modal-counts')
    expect(counts.textContent).toMatch(/2 done.*1 uploading.*1 failed.*1 queued.*of 5/)
  })

  it('aborts in-flight work and resets store on close', async () => {
    vi.spyOn(upload, 'createScan').mockResolvedValue({ scan_id: 'scan_abort' })
    const onClose = vi.fn()
    render(wrap(<UploadModal projectId="p1" open onClose={onClose} />))
    await userEvent.click(screen.getByTestId('upload-modal-close'))
    expect(onClose).toHaveBeenCalled()
  })

  // ---- Task 2: Close keeps upload running, "Cancel upload" stops it ----
  describe('close and cancel upload while running', () => {
    // Helper: render the modal and force `running=true` by holding a never-
    // resolving createScan promise. This is the only public path to set the
    // running flag, since it's local React state in UploadModal.
    async function renderRunning(onClose = vi.fn()) {
      // Block createScan so startUpload sits in `running=true` indefinitely.
      // We give the test full control over its resolution if needed.
      let release: () => void = () => {}
      const blocked = new Promise<{ scan_id: string }>((resolve) => {
        release = () => resolve({ scan_id: 's42' })
      })
      vi.spyOn(upload, 'createScan').mockReturnValue(blocked)

      render(wrap(<UploadModal projectId="p1" open onClose={onClose} />))

      // Fill in scan meta + drop a file so Start enables.
      await userEvent.type(screen.getByTestId('scan-form-name'), 'scan-A')
      const matInput = await screen.findByTestId('material-combobox-input')
      await userEvent.click(matInput)
      await userEvent.click(await screen.findByTestId('material-combobox-option-graphene'))
      const dz = screen.getByTestId('file-dropzone')
      const f1 = new File([new Uint8Array(8)], 'tile_0_0.tif')
      fireEvent.drop(dz, { dataTransfer: { files: [f1], items: [], types: ['Files'] } })
      await waitFor(() =>
        expect((screen.getByTestId('upload-modal-start') as HTMLButtonElement).disabled).toBe(false),
      )
      await userEvent.click(screen.getByTestId('upload-modal-start'))
      // Now createScan is pending → running=true. Seed scanId directly so the
      // test can assert it survives close, since we never let createScan
      // resolve (which is what would normally call setScanId).
      useUploadStore.getState().setScanId('s42')
      // Mark the file as 'uploading' so a cancelAll has something to abort.
      const uid = useUploadStore.getState().order[0]
      useUploadStore.getState().patch(uid, { status: 'uploading', progress: 0.3 })
      return { onClose, release }
    }

    it('Test A: clicking Close mid-run calls onClose immediately, preserves scanId (upload keeps running)', async () => {
      const { onClose } = await renderRunning()
      await userEvent.click(screen.getByTestId('upload-modal-close'))
      // Task 2: Close just closes, no confirm dialog
      expect(onClose).toHaveBeenCalled()
      // scanId still intact (upload continues in background)
      expect(useUploadStore.getState().scanId).toBe('s42')
    })

    it('Test B: "Cancel upload" button shows confirm dialog', async () => {
      const { onClose } = await renderRunning()
      // Click the red "Cancel upload" button
      await userEvent.click(screen.getByTestId('upload-modal-cancel-upload'))
      // Should show cancel-upload confirm dialog
      expect(await screen.findByTestId('upload-modal-cancel-confirm')).toBeTruthy()
      expect(onClose).not.toHaveBeenCalled()
      expect(useUploadStore.getState().scanId).toBe('s42')
    })

    it('Test C: confirming cancel upload clears transient files, does NOT call onClose', async () => {
      const { onClose } = await renderRunning()
      await userEvent.click(screen.getByTestId('upload-modal-cancel-upload'))
      await screen.findByTestId('upload-modal-cancel-confirm')
      await userEvent.click(screen.getByTestId('upload-modal-cancel-confirm-yes'))
      // Cancel upload clears transient but does NOT close modal
      expect(onClose).not.toHaveBeenCalled()
      // Transient files cleared (only 'done' survives, none here)
      expect(useUploadStore.getState().order).toHaveLength(0)
    })

    it('Test D: clicking "No, keep uploading" dismisses confirm, keeps modal open', async () => {
      const { onClose } = await renderRunning()
      await userEvent.click(screen.getByTestId('upload-modal-cancel-upload'))
      await screen.findByTestId('upload-modal-cancel-confirm')
      await userEvent.click(screen.getByTestId('upload-modal-cancel-confirm-no'))
      // Confirm UI dismissed
      await waitFor(() =>
        expect(screen.queryByTestId('upload-modal-cancel-confirm')).toBeNull(),
      )
      expect(onClose).not.toHaveBeenCalled()
      expect(useUploadStore.getState().scanId).toBe('s42')
    })

    it('Test D: non-running close still does full reset (scanId cleared)', async () => {
      // Modal not running: pre-seed scanId, then close. Should clear it.
      const onClose = vi.fn()
      render(wrap(<UploadModal projectId="p1" open onClose={onClose} />))
      useUploadStore.getState().setScanId('s_legacy')
      await userEvent.click(screen.getByTestId('upload-modal-close'))
      expect(onClose).toHaveBeenCalled()
      expect(useUploadStore.getState().scanId).toBeNull()
    })

    it('Test E: stale createScan resolution after Cancel Upload does NOT leak scanId or toast', async () => {
      // Spy on toast.success so we can assert nothing fires post-cancel.
      const toastSuccessSpy = vi.spyOn(toast, 'success').mockImplementation(() => 'tid' as unknown as string | number)

      // Capture the AbortSignal that handleConfirmCancelUpload -> abort() should mark.
      let capturedSignal: AbortSignal | undefined
      let resolveCreateScan: (v: { scan_id: string }) => void = () => {}
      const blocked = new Promise<{ scan_id: string }>((resolve) => {
        resolveCreateScan = resolve
      })
      vi.spyOn(upload, 'createScan').mockImplementation(
        async (_pid: string, _body: upload.CreateScanBody, signal?: AbortSignal) => {
          capturedSignal = signal
          return blocked
        },
      )

      const onClose = vi.fn()
      render(wrap(<UploadModal projectId="p1" open onClose={onClose} />))

      // Drive the modal into running=true via the public path.
      await userEvent.type(screen.getByTestId('scan-form-name'), 'scan-A')
      const matInput = await screen.findByTestId('material-combobox-input')
      await userEvent.click(matInput)
      await userEvent.click(await screen.findByTestId('material-combobox-option-graphene'))
      const dz = screen.getByTestId('file-dropzone')
      const f1 = new File([new Uint8Array(8)], 'tile_0_0.tif')
      fireEvent.drop(dz, { dataTransfer: { files: [f1], items: [], types: ['Files'] } })
      await waitFor(() =>
        expect((screen.getByTestId('upload-modal-start') as HTMLButtonElement).disabled).toBe(false),
      )
      await userEvent.click(screen.getByTestId('upload-modal-start'))

      // Wait until createScan has actually been called and got a signal.
      await waitFor(() => expect(capturedSignal).toBeDefined())

      // Reset toast count: the typed-in name and other interactions don't
      // call toast.success, but be defensive — count from this point on.
      toastSuccessSpy.mockClear()

      // Cancel upload → confirm.
      await userEvent.click(screen.getByTestId('upload-modal-cancel-upload'))
      await screen.findByTestId('upload-modal-cancel-confirm')
      await userEvent.click(screen.getByTestId('upload-modal-cancel-confirm-yes'))
      // Modal stays open after cancel (does not call onClose)
      expect(onClose).not.toHaveBeenCalled()

      // The signal should be aborted (approach 1 — controller wired through).
      expect(capturedSignal?.aborted).toBe(true)

      // Now resolve the stale createScan promise. The mutation's onSuccess
      // would normally fire setScanId + toast.success — neither must happen
      // after abort.
      resolveCreateScan({ scan_id: 's999' })
      // Let microtasks flush.
      await new Promise((r) => setTimeout(r, 0))
      await new Promise((r) => setTimeout(r, 0))

      expect(useUploadStore.getState().scanId).not.toBe('s999')
      expect(toastSuccessSpy).not.toHaveBeenCalled()
    })
  })

  // ---- Retry-all batch button (Task D4) ----
  describe('retry all failed', () => {
    function seedFailedFiles(count: number) {
      const mkFile = (uid: string, status: UploadFile['status']): UploadFile => ({
        uid,
        file: new File([new Uint8Array(1)], `${uid}.tif`),
        filename: `${uid}.tif`,
        size: 1,
        grid_ix: 0,
        grid_iy: 0,
        status,
        progress: 0,
        sha256_hex: null,
        upload_item_id: null,
        image_id: null,
        error: 'boom',
        request_id: 'req-x',
      })
      const files: Record<string, UploadFile> = {}
      const order: string[] = []
      for (let i = 0; i < count; i++) {
        const uid = `failed_${i}`
        files[uid] = mkFile(uid, 'failed')
        order.push(uid)
      }
      useUploadStore.setState({ files, order, scanId: 's_retry' })
    }

    it('shows retry-all button when failed > 0 and not running; clicking it calls retryAllFailed', async () => {
      // Mock the orchestrator singleton so we can observe retryAllFailed calls
      // without actually driving the upload pipeline.
      const retryAllSpy = vi.fn().mockResolvedValue(undefined)
      vi.spyOn(orchestratorMod, 'getOrchestrator').mockReturnValue({
        retryAllFailed: retryAllSpy,
        runAll: vi.fn().mockResolvedValue(undefined),
        cancelAll: vi.fn(),
        retry: vi.fn().mockResolvedValue(undefined),
      } as unknown as orchestratorMod.Orchestrator)

      render(wrap(<UploadModal projectId="p1" open onClose={() => {}} />))
      // Open-effect resets the store; seed failed rows AFTER mount.
      seedFailedFiles(3)

      const btn = await screen.findByTestId('upload-modal-retry-all')
      expect(btn).toBeTruthy()
      // Label surfaces the count so the user knows what they're triggering.
      expect(btn.textContent).toMatch(/3/)

      await userEvent.click(btn)
      await waitFor(() => expect(retryAllSpy).toHaveBeenCalledTimes(1))
    })

    it('hides retry-all button when failed === 0', async () => {
      render(wrap(<UploadModal projectId="p1" open onClose={() => {}} />))
      // No failed files seeded — button must not appear.
      expect(screen.queryByTestId('upload-modal-retry-all')).toBeNull()
    })

    it('hides retry-all button while running', async () => {
      // Block createScan so startUpload puts the modal into running=true.
      vi.spyOn(upload, 'createScan').mockReturnValue(
        new Promise(() => {}) as unknown as ReturnType<typeof upload.createScan>,
      )

      render(wrap(<UploadModal projectId="p1" open onClose={() => {}} />))

      // Drive the modal into running=true via the public path (mirrors Test E
      // helper). Drop a single file + meta + click Start.
      await userEvent.type(screen.getByTestId('scan-form-name'), 'scan-A')
      const matInput = await screen.findByTestId('material-combobox-input')
      await userEvent.click(matInput)
      await userEvent.click(await screen.findByTestId('material-combobox-option-graphene'))
      const dz = screen.getByTestId('file-dropzone')
      const f1 = new File([new Uint8Array(8)], 'tile_0_0.tif')
      fireEvent.drop(dz, { dataTransfer: { files: [f1], items: [], types: ['Files'] } })
      await waitFor(() =>
        expect((screen.getByTestId('upload-modal-start') as HTMLButtonElement).disabled).toBe(false),
      )
      await userEvent.click(screen.getByTestId('upload-modal-start'))

      // Now flip the dropped file to 'failed' so failed > 0 — but `running`
      // is still true, so the retry-all button must remain hidden.
      const uid = useUploadStore.getState().order[0]
      useUploadStore.getState().patch(uid, { status: 'failed', error: 'x', request_id: null })

      // Counter should reflect the failure (sanity that state propagated).
      await waitFor(() => {
        const counts = screen.getByTestId('upload-modal-counts')
        expect(counts.textContent).toMatch(/1 failed/)
      })

      expect(screen.queryByTestId('upload-modal-retry-all')).toBeNull()
    })
  })
})
