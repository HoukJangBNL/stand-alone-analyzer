import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useUploadStore, resetUploadStore } from '@/state/uploadSlice'
import { Orchestrator } from '@/lib/uploadOrchestrator'
import * as upload from '@/api/upload'
import * as sha from '@/lib/sha256'

beforeEach(() => {
  resetUploadStore()
  vi.restoreAllMocks()
  // jsdom lacks createImageBitmap; stub the orchestrator's dimension reader.
  ;(globalThis as { __readImageDimensionsForTest?: (f: File) => Promise<{ width: number; height: number }> })
    .__readImageDimensionsForTest = async () => ({ width: 100, height: 100 })
})
afterEach(() => {
  vi.restoreAllMocks()
  delete (globalThis as { __readImageDimensionsForTest?: unknown }).__readImageDimensionsForTest
})

function fakeFile(name: string, bytes = 4): File {
  return new File([new Uint8Array(bytes)], name)
}

describe('Orchestrator', () => {
  it('runs hash → presign → PUT → complete and ends in done', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('a'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockResolvedValue({
      put_url: 'http://s3.fake/put',
      headers: { 'x-amz-checksum-sha256': 'b64' },
      upload_item_id: 'ui_1',
    })
    vi.spyOn(upload, 'putToS3').mockResolvedValue(undefined)
    vi.spyOn(upload, 'completeImage').mockResolvedValue({ image_id: 'img_1' })

    useUploadStore.getState().setScanId('scan_1')
    useUploadStore.getState().addFiles([fakeFile('tile_0_0.tif')])
    const uid = useUploadStore.getState().order[0]
    useUploadStore.getState().setGrid(uid, 0, 0)

    const orch = new Orchestrator({ concurrency: 4 })
    await orch.runAll()

    const f = useUploadStore.getState().files[uid]
    expect(f.status).toBe('done')
    expect(f.upload_item_id).toBe('ui_1')
    expect(f.image_id).toBe('img_1')
    expect(f.sha256_hex).toBe('a'.repeat(64))
  })

  it('marks file failed when presign throws and preserves error message', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('c'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockRejectedValue(new Error('presign 409 duplicate'))

    useUploadStore.getState().setScanId('scan_1')
    useUploadStore.getState().addFiles([fakeFile('tile_1_2.tif')])
    const uid = useUploadStore.getState().order[0]
    useUploadStore.getState().setGrid(uid, 1, 2)

    const orch = new Orchestrator({ concurrency: 4 })
    await orch.runAll()

    const f = useUploadStore.getState().files[uid]
    expect(f.status).toBe('failed')
    expect(f.error).toMatch(/presign 409 duplicate/)
  })

  it('respects the concurrency limit (max 4 in flight)', async () => {
    let inFlight = 0
    let peak = 0
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('d'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockImplementation(async () => {
      inFlight++
      peak = Math.max(peak, inFlight)
      await new Promise((r) => setTimeout(r, 10))
      inFlight--
      return { put_url: 'http://s3.fake/put', headers: {}, upload_item_id: 'x' }
    })
    vi.spyOn(upload, 'putToS3').mockResolvedValue(undefined)
    vi.spyOn(upload, 'completeImage').mockResolvedValue({ image_id: 'img' })

    useUploadStore.getState().setScanId('scan_1')
    const files: File[] = []
    for (let i = 0; i < 10; i++) files.push(fakeFile(`tile_0_${i}.tif`))
    useUploadStore.getState().addFiles(files)
    for (const uid of useUploadStore.getState().order) {
      useUploadStore.getState().setGrid(uid, 0, 0)
    }

    const orch = new Orchestrator({ concurrency: 4 })
    await orch.runAll()
    expect(peak).toBeLessThanOrEqual(4)
  })

  it('cancel() aborts in-flight work and the file lands non-done', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('e'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockImplementation(async (_sid, _body, signal) => {
      return new Promise((_resolve, reject) => {
        signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    })

    useUploadStore.getState().setScanId('scan_1')
    useUploadStore.getState().addFiles([fakeFile('tile_0_0.tif')])
    const uid = useUploadStore.getState().order[0]
    useUploadStore.getState().setGrid(uid, 0, 0)

    const orch = new Orchestrator({ concurrency: 4 })
    const p = orch.runAll()
    // give it a microtask to start, then cancel
    await new Promise((r) => setTimeout(r, 5))
    orch.cancelAll()
    await p

    const f = useUploadStore.getState().files[uid]
    expect(f.status).not.toBe('done')
  })
})
