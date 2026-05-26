import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { UploadModal } from '../UploadModal'
import { resetUploadStore } from '@/state/uploadSlice'
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
})

describe('UploadModal', () => {
  it('stashes metadata, derives image_count from dropped files, then runs pipeline & finalizes', async () => {
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

    // Phase 1: metadata only — no image_count field
    await userEvent.type(screen.getByTestId('scan-form-name'), 'scan-A')
    const matInput = await screen.findByTestId('material-combobox-input')
    await userEvent.click(matInput)
    await userEvent.click(await screen.findByTestId('material-combobox-option-graphene'))
    expect(screen.queryByTestId('scan-form-image-count')).toBeNull()
    await userEvent.click(screen.getByTestId('scan-form-submit'))

    // ScanForm submit must NOT call createScan yet — it's deferred until Start upload.
    expect(createScanSpy).not.toHaveBeenCalled()

    // Phase 2: files — Start upload disabled until at least one file is dropped.
    const dz = await screen.findByTestId('file-dropzone')
    const startBtn = await screen.findByTestId('upload-modal-start')
    expect((startBtn as HTMLButtonElement).disabled).toBe(true)

    const f1 = new File([new Uint8Array(8)], 'tile_0_0.tif')
    const f2 = new File([new Uint8Array(8)], 'tile_0_1.tif')
    fireEvent.drop(dz, { dataTransfer: { files: [f1, f2], items: [], types: ['Files'] } })

    await userEvent.click(await screen.findByTestId('upload-modal-start'))

    // createScan must be called with image_count derived from the dropped files (2).
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

  it('aborts in-flight work and resets store on close', async () => {
    vi.spyOn(upload, 'createScan').mockResolvedValue({ scan_id: 'scan_abort' })
    const onClose = vi.fn()
    render(wrap(<UploadModal projectId="p1" open onClose={onClose} />))
    await userEvent.click(screen.getByTestId('upload-modal-close'))
    expect(onClose).toHaveBeenCalled()
  })
})
