import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useUploadStore, resetUploadStore } from '@/state/uploadSlice'
import { Orchestrator } from '@/lib/uploadOrchestrator'
import * as upload from '@/api/upload'
import * as sha from '@/lib/sha256'
import { ApiError } from '@/api/selector'

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

  it('captures request_id from ApiError into the failed file row', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('f'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockRejectedValue(
      new ApiError(500, 'INTERNAL', 'boom', null, 'req-123'),
    )

    useUploadStore.getState().setScanId('scan_1')
    useUploadStore.getState().addFiles([fakeFile('tile_3_4.tif')])
    const uid = useUploadStore.getState().order[0]
    useUploadStore.getState().setGrid(uid, 3, 4)

    const orch = new Orchestrator({ concurrency: 4 })
    await orch.runAll()

    const f = useUploadStore.getState().files[uid]
    expect(f.status).toBe('failed')
    expect(f.error).toMatch(/boom/)
    expect(f.request_id).toBe('req-123')
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

  it('runAll resumes after cancelAll (cancelled flag is reset)', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('a'.repeat(64))
    // First call to presign hangs until aborted; subsequent calls resolve.
    let presignCalls = 0
    vi.spyOn(upload, 'presignImage').mockImplementation(async (_sid, _body, signal) => {
      presignCalls++
      if (presignCalls === 1) {
        return new Promise((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        })
      }
      return { put_url: 'http://s3.fake/put', headers: {}, upload_item_id: `ui_${presignCalls}` }
    })
    vi.spyOn(upload, 'putToS3').mockResolvedValue(undefined)
    vi.spyOn(upload, 'completeImage').mockImplementation(async (_sid, uiid) => ({
      image_id: `img_${uiid}`,
    }))

    useUploadStore.getState().setScanId('scan_1')
    // Use concurrency=1 so file #2 stays 'queued' while file #1 is hung in presign.
    useUploadStore
      .getState()
      .addFiles([fakeFile('tile_0_0.tif'), fakeFile('tile_0_1.tif')])
    const order = useUploadStore.getState().order
    for (const uid of order) useUploadStore.getState().setGrid(uid, 0, 0)

    const orch = new Orchestrator({ concurrency: 1 })
    const runP = orch.runAll()
    // Let worker enter presign on file #1, then cancel.
    await new Promise((r) => setTimeout(r, 5))
    orch.cancelAll()
    await runP

    // File #1 should be non-done (cancelled mid-presign); file #2 still queued.
    expect(useUploadStore.getState().files[order[0]].status).not.toBe('done')
    expect(useUploadStore.getState().files[order[1]].status).toBe('queued')

    // Second runAll must NOT exit immediately — the cancelled flag has to reset.
    await orch.runAll()

    // File #2 (still queued) should now be done.
    expect(useUploadStore.getState().files[order[1]].status).toBe('done')
  })

  it('retryAllFailed flips all failed rows to queued and runs them', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('a'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockResolvedValue({
      put_url: 'http://s3.fake/put',
      headers: {},
      upload_item_id: 'ui_retry',
    })
    vi.spyOn(upload, 'putToS3').mockResolvedValue(undefined)
    vi.spyOn(upload, 'completeImage').mockResolvedValue({ image_id: 'img_retry' })

    useUploadStore.getState().setScanId('scan_retry')
    useUploadStore
      .getState()
      .addFiles([fakeFile('tile_0_0.tif'), fakeFile('tile_0_1.tif'), fakeFile('tile_0_2.tif')])
    const order = [...useUploadStore.getState().order]
    // Seed: all three are failed, with non-null error and request_id, plus
    // grids set so runOne won't bail on the grid_ix/grid_iy guard.
    for (const uid of order) {
      useUploadStore.getState().setGrid(uid, 0, 0)
      useUploadStore.getState().patch(uid, {
        status: 'failed',
        error: 'previous failure',
        request_id: 'req-old',
      })
    }

    const orch = new Orchestrator({ concurrency: 4 })
    await orch.retryAllFailed()

    for (const uid of order) {
      const f = useUploadStore.getState().files[uid]
      expect(f.status).toBe('done')
      expect(f.error).toBeNull()
      expect(f.request_id).toBeNull()
    }
  })

  it('retryAllFailed leaves non-failed rows untouched', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('a'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockResolvedValue({
      put_url: 'http://s3.fake/put',
      headers: {},
      upload_item_id: 'ui_x',
    })
    vi.spyOn(upload, 'putToS3').mockResolvedValue(undefined)
    vi.spyOn(upload, 'completeImage').mockResolvedValue({ image_id: 'img_x' })

    useUploadStore.getState().setScanId('scan_mix')
    useUploadStore
      .getState()
      .addFiles([fakeFile('tile_0_0.tif'), fakeFile('tile_0_1.tif')])
    const order = [...useUploadStore.getState().order]
    // First row stays 'done' — it must NOT be re-run; second row is failed.
    useUploadStore.getState().setGrid(order[0], 0, 0)
    useUploadStore.getState().patch(order[0], {
      status: 'done',
      image_id: 'img_pre',
      upload_item_id: 'ui_pre',
    })
    useUploadStore.getState().setGrid(order[1], 0, 1)
    useUploadStore.getState().patch(order[1], {
      status: 'failed',
      error: 'old',
      request_id: 'req-old',
    })

    const orch = new Orchestrator({ concurrency: 4 })
    await orch.retryAllFailed()

    // The 'done' row's terminal id is preserved (proof we didn't re-run it).
    expect(useUploadStore.getState().files[order[0]].status).toBe('done')
    expect(useUploadStore.getState().files[order[0]].image_id).toBe('img_pre')
    // The failed row got retried and is now done.
    expect(useUploadStore.getState().files[order[1]].status).toBe('done')
    expect(useUploadStore.getState().files[order[1]].error).toBeNull()
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
