// web/src/lib/uploadOrchestrator.ts
import { useUploadStore, type UploadFile } from '@/state/uploadSlice'
import { sha256Hex } from '@/lib/sha256'
import { presignImage, putToS3, completeImage, type PresignBody } from '@/api/upload'

export interface OrchestratorOptions {
  concurrency?: number
}

/**
 * Decode just enough of the file to read width/height. Uses the cheap
 * createImageBitmap path when available and falls back to <img>. Tests
 * provide a stub via globalThis.__readImageDimensionsForTest.
 */
async function readImageDimensions(file: File): Promise<{ width: number; height: number }> {
  const stub = (globalThis as { __readImageDimensionsForTest?: (f: File) => Promise<{ width: number; height: number }> })
    .__readImageDimensionsForTest
  if (stub) return stub(file)
  if (typeof createImageBitmap === 'function') {
    const bmp = await createImageBitmap(file)
    try {
      return { width: bmp.width, height: bmp.height }
    } finally {
      bmp.close?.()
    }
  }
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      const out = { width: img.naturalWidth, height: img.naturalHeight }
      URL.revokeObjectURL(url)
      resolve(out)
    }
    img.onerror = (err) => {
      URL.revokeObjectURL(url)
      reject(err instanceof Error ? err : new Error('image decode failed'))
    }
    img.src = url
  })
}

/**
 * Per-file pipeline driver. Owns one AbortController per file and the
 * concurrency semaphore. Reads/mutates `useUploadStore` directly — no other
 * code in the app should write to file.status.
 */
export class Orchestrator {
  private readonly concurrency: number
  private readonly controllers = new Map<string, AbortController>()
  private cancelled = false

  constructor(opts: OrchestratorOptions = {}) {
    this.concurrency = opts.concurrency ?? 4
  }

  /** Run every queued file through the pipeline. Resolves when all settle. */
  async runAll(): Promise<void> {
    const store = useUploadStore.getState()
    const scanId = store.scanId
    if (!scanId) throw new Error('Orchestrator.runAll: scanId not set')

    const queue = store.order
      .map((uid) => store.files[uid])
      .filter((f) => f.status === 'queued')

    let cursor = 0
    const workers: Promise<void>[] = []
    const next = async (): Promise<void> => {
      while (!this.cancelled) {
        const i = cursor++
        if (i >= queue.length) return
        await this.runOne(scanId, queue[i].uid)
      }
    }
    for (let i = 0; i < this.concurrency; i++) workers.push(next())
    await Promise.all(workers)
  }

  /** Cancel every in-flight fetch and stop new work. */
  cancelAll(): void {
    this.cancelled = true
    for (const c of this.controllers.values()) c.abort()
    this.controllers.clear()
  }

  /** Retry a single failed file. */
  async retry(uid: string): Promise<void> {
    const store = useUploadStore.getState()
    const scanId = store.scanId
    if (!scanId) throw new Error('Orchestrator.retry: scanId not set')
    store.patch(uid, { status: 'queued', error: null, progress: 0 })
    await this.runOne(scanId, uid)
  }

  private async runOne(scanId: string, uid: string): Promise<void> {
    const ctrl = new AbortController()
    this.controllers.set(uid, ctrl)
    const store = useUploadStore.getState()
    const f = store.files[uid]
    if (!f) return
    if (f.grid_ix === null || f.grid_iy === null) {
      store.patch(uid, { status: 'failed', error: 'grid_ix/grid_iy required' })
      this.controllers.delete(uid)
      return
    }

    try {
      // 1) hash
      store.patch(uid, { status: 'hashing' })
      const hex = await sha256Hex(f.file)
      if (this.cancelled || ctrl.signal.aborted) {
        throw new DOMException('aborted', 'AbortError')
      }
      store.patch(uid, { sha256_hex: hex })

      // 2) presign
      store.patch(uid, { status: 'presigning' })
      const body: PresignBody = {
        filename: f.filename,
        sha256: hex,
        size_bytes: f.size,
        grid_ix: f.grid_ix,
        grid_iy: f.grid_iy,
      }
      const pre = await presignImage(scanId, body, ctrl.signal)
      store.patch(uid, { upload_item_id: pre.upload_item_id })

      // 3) PUT to S3
      store.patch(uid, { status: 'uploading', progress: 0 })
      await putToS3(pre.put_url, f.file, pre.headers, ctrl.signal)
      store.patch(uid, { progress: 1 })

      // 4) complete — server requires pixel dimensions, so decode locally.
      store.patch(uid, { status: 'completing' })
      const dims = await readImageDimensions(f.file)
      const cmp = await completeImage(
        scanId,
        pre.upload_item_id,
        { width: dims.width, height: dims.height },
        ctrl.signal,
      )
      store.patch(uid, { status: 'done', image_id: cmp.image_id })
    } catch (e: unknown) {
      const msg = (e as { message?: string })?.message ?? String(e)
      const aborted = (e as { name?: string })?.name === 'AbortError'
      store.patch(uid, {
        status: 'failed',
        error: aborted ? 'aborted' : msg,
      })
    } finally {
      this.controllers.delete(uid)
    }
  }
}

/** Module-scope singleton — UploadModal owns its lifecycle. */
let current: Orchestrator | null = null
export function getOrchestrator(): Orchestrator {
  if (!current) current = new Orchestrator({ concurrency: 4 })
  return current
}
export function resetOrchestrator(): void {
  current?.cancelAll()
  current = null
}

export function isFinalFile(f: UploadFile): boolean {
  return f.status === 'done' || f.status === 'failed'
}
