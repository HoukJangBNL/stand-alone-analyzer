import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { UploadModal } from '../UploadModal'
import { resetUploadStore, useUploadStore, type UploadFile } from '@/state/uploadSlice'
import * as upload from '@/api/upload'
import * as materials from '@/api/materials'
import * as sha from '@/lib/sha256'

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
})
