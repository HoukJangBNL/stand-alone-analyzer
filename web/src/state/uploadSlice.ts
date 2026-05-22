// web/src/state/uploadSlice.ts
import { create } from 'zustand'

export type FileStatus =
  | 'queued'
  | 'hashing'
  | 'presigning'
  | 'uploading'
  | 'completing'
  | 'done'
  | 'failed'

export interface UploadFile {
  uid: string // local-only id, NOT upload_item_id
  file: File
  filename: string
  size: number
  grid_ix: number | null
  grid_iy: number | null
  status: FileStatus
  progress: number // 0..1, only meaningful during 'uploading'
  sha256_hex: string | null
  upload_item_id: string | null
  image_id: string | null
  error: string | null
}

export interface UploadState {
  scanId: string | null
  files: Record<string, UploadFile>
  order: string[] // stable display order

  setScanId(id: string | null): void
  addFiles(files: File[]): void
  setGrid(uid: string, ix: number | null, iy: number | null): void
  removeFile(uid: string): void
  patch(uid: string, patch: Partial<UploadFile>): void
  reset(): void
}

function genUid(): string {
  return `f_${Math.random().toString(36).slice(2, 10)}`
}

/** Auto-detect (ix, iy) from filename pattern `tile_{ix}_{iy}.<ext>`. */
const FILENAME_GRID_RE = /^.*tile_(\d+)_(\d+)\..*$/i
export function detectGrid(filename: string): { ix: number | null; iy: number | null } {
  const m = filename.match(FILENAME_GRID_RE)
  if (!m) return { ix: null, iy: null }
  return { ix: parseInt(m[1], 10), iy: parseInt(m[2], 10) }
}

export const useUploadStore = create<UploadState>((set) => ({
  scanId: null,
  files: {},
  order: [],

  setScanId(id) {
    set({ scanId: id })
  },
  addFiles(files) {
    set((s) => {
      const nextFiles = { ...s.files }
      const nextOrder = [...s.order]
      for (const f of files) {
        const uid = genUid()
        const { ix, iy } = detectGrid(f.name)
        nextFiles[uid] = {
          uid,
          file: f,
          filename: f.name,
          size: f.size,
          grid_ix: ix,
          grid_iy: iy,
          status: 'queued',
          progress: 0,
          sha256_hex: null,
          upload_item_id: null,
          image_id: null,
          error: null,
        }
        nextOrder.push(uid)
      }
      return { files: nextFiles, order: nextOrder }
    })
  },
  setGrid(uid, ix, iy) {
    set((s) => {
      const cur = s.files[uid]
      if (!cur) return s
      return { files: { ...s.files, [uid]: { ...cur, grid_ix: ix, grid_iy: iy } } }
    })
  },
  removeFile(uid) {
    set((s) => {
      const { [uid]: _drop, ...rest } = s.files
      return { files: rest, order: s.order.filter((x) => x !== uid) }
    })
  },
  patch(uid, p) {
    set((s) => {
      const cur = s.files[uid]
      if (!cur) return s
      return { files: { ...s.files, [uid]: { ...cur, ...p } } }
    })
  },
  reset() {
    set({ scanId: null, files: {}, order: [] })
  },
}))

export function resetUploadStore(): void {
  useUploadStore.getState().reset()
}
