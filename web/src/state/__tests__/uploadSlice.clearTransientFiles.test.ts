import { describe, it, expect, beforeEach } from 'vitest'
import { useUploadStore, resetUploadStore, type UploadFile } from '@/state/uploadSlice'

function mkFile(uid: string, status: UploadFile['status']): UploadFile {
  return {
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
  }
}

beforeEach(() => {
  resetUploadStore()
})

describe('uploadSlice.clearTransientFiles', () => {
  it('drops queued/uploading/failed rows, keeps done rows; preserves scanId', () => {
    const entries: Array<[string, UploadFile['status']]> = [
      ['f1', 'done'],
      ['f2', 'uploading'],
      ['f3', 'queued'],
      ['f4', 'failed'],
      ['f5', 'done'],
    ]
    const files: Record<string, UploadFile> = {}
    const order: string[] = []
    for (const [uid, st] of entries) {
      files[uid] = mkFile(uid, st)
      order.push(uid)
    }
    useUploadStore.setState({ files, order, scanId: 's42' })

    useUploadStore.getState().clearTransientFiles()

    const after = useUploadStore.getState()
    expect(after.scanId).toBe('s42')
    // Only done rows survive.
    expect(after.order).toEqual(['f1', 'f5'])
    expect(Object.keys(after.files).sort()).toEqual(['f1', 'f5'])
    // Every surviving order entry resolves to a file (no dangling refs).
    for (const uid of after.order) {
      expect(after.files[uid]).toBeDefined()
      expect(after.files[uid].status).toBe('done')
    }
  })

  it('preserves scanId when nothing is done (results in empty maps)', () => {
    const files: Record<string, UploadFile> = {
      f1: mkFile('f1', 'queued'),
      f2: mkFile('f2', 'failed'),
    }
    useUploadStore.setState({ files, order: ['f1', 'f2'], scanId: 's_only' })

    useUploadStore.getState().clearTransientFiles()

    const after = useUploadStore.getState()
    expect(after.scanId).toBe('s_only')
    expect(after.order).toEqual([])
    expect(after.files).toEqual({})
  })

  it('is a no-op (other than identity) on empty state', () => {
    // Fresh store after resetUploadStore: scanId=null, files={}, order=[].
    expect(() => useUploadStore.getState().clearTransientFiles()).not.toThrow()
    const after = useUploadStore.getState()
    expect(after.scanId).toBeNull()
    expect(after.order).toEqual([])
    expect(after.files).toEqual({})
  })

  it('preserves intermediate pipeline statuses are dropped (hashing/presigning/completing)', () => {
    // These transient statuses should also NOT survive — only `done` does.
    const entries: Array<[string, UploadFile['status']]> = [
      ['f1', 'hashing'],
      ['f2', 'presigning'],
      ['f3', 'completing'],
      ['f4', 'done'],
    ]
    const files: Record<string, UploadFile> = {}
    const order: string[] = []
    for (const [uid, st] of entries) {
      files[uid] = mkFile(uid, st)
      order.push(uid)
    }
    useUploadStore.setState({ files, order, scanId: 'sX' })

    useUploadStore.getState().clearTransientFiles()

    const after = useUploadStore.getState()
    expect(after.order).toEqual(['f4'])
    expect(Object.keys(after.files)).toEqual(['f4'])
    expect(after.scanId).toBe('sX')
  })
})
