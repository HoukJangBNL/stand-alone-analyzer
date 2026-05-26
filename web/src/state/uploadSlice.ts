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
  /** ApiError request_id surfaced when the file fails. Null otherwise. */
  request_id: string | null
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
  /**
   * Drop every file row that is NOT in `done` status, preserving `scanId`
   * and the already-uploaded `done` rows. Used by UploadModal when the user
   * confirms "Stop & Close" mid-upload — the in-flight/queued/failed rows are
   * client-side throwaways, while server-side scan + done rows must survive
   * so the user could potentially re-open and resume later.
   */
  clearTransientFiles(): void
}

function genUid(): string {
  return `f_${Math.random().toString(36).slice(2, 10)}`
}

/**
 * Auto-detect (ix, iy) from common scan filename patterns.
 *
 * Supported (case-insensitive):
 *   - `tile_3_5.tif`               → tile_<ix>_<iy>
 *   - `ix002_iy025.png`            → ix<ix>_iy<iy> (scanner default)
 *   - `scan_ix12_iy7_extra.png`    → ix<ix>...iy<iy> embedded
 *
 * The `ix...iy` pattern wins over the `tile_` pattern when both could match.
 */
const IX_IY_RE = /ix(\d+).*?iy(\d+)/i
const TILE_RE = /tile[_-](\d+)[_-](\d+)/i
export function detectGrid(filename: string): { ix: number | null; iy: number | null } {
  const ixIy = filename.match(IX_IY_RE)
  if (ixIy) return { ix: parseInt(ixIy[1], 10), iy: parseInt(ixIy[2], 10) }
  const tile = filename.match(TILE_RE)
  if (tile) return { ix: parseInt(tile[1], 10), iy: parseInt(tile[2], 10) }
  return { ix: null, iy: null }
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
          request_id: null,
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
  clearTransientFiles() {
    set((s) => {
      const nextFiles: Record<string, UploadFile> = {}
      const nextOrder: string[] = []
      for (const uid of s.order) {
        const f = s.files[uid]
        if (f && f.status === 'done') {
          nextFiles[uid] = f
          nextOrder.push(uid)
        }
      }
      return { files: nextFiles, order: nextOrder }
    })
  },
}))

export function resetUploadStore(): void {
  useUploadStore.getState().reset()
}
