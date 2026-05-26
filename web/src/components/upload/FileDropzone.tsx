// web/src/components/upload/FileDropzone.tsx
import { useRef, useState, type DragEvent } from 'react'
import { useUploadStore } from '@/state/uploadSlice'

// Same set as run_amg_v2.py:45 — case-insensitive.
const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp']
function isImage(name: string): boolean {
  const lower = name.toLowerCase()
  return IMAGE_EXTS.some((ext) => lower.endsWith(ext))
}

// Minimal local types for the WebKit FileSystem API (DOM lib doesn't ship full defs).
type FsEntry = {
  isFile: boolean
  isDirectory: boolean
  name: string
  file?: (cb: (f: File) => void, err?: (e: unknown) => void) => void
  createReader?: () => {
    readEntries: (cb: (entries: FsEntry[]) => void, err?: (e: unknown) => void) => void
  }
}

function entryToFile(entry: FsEntry): Promise<File | null> {
  return new Promise((resolve) => {
    if (!entry.file) return resolve(null)
    entry.file(
      (f) => resolve(f),
      () => resolve(null),
    )
  })
}

function readAllEntries(entry: FsEntry): Promise<FsEntry[]> {
  return new Promise((resolve) => {
    const reader = entry.createReader?.()
    if (!reader) return resolve([])
    const acc: FsEntry[] = []
    const pump = () => {
      reader.readEntries(
        (batch) => {
          if (batch.length === 0) return resolve(acc)
          acc.push(...batch)
          pump() // readEntries returns in chunks; keep reading until empty.
        },
        () => resolve(acc),
      )
    }
    pump()
  })
}

interface ScanReporter {
  onFile(): void
  onDir(): void
}

async function walkEntry(entry: FsEntry, report: ScanReporter): Promise<File[]> {
  if (entry.isFile) {
    const f = await entryToFile(entry)
    if (f && isImage(f.name)) {
      report.onFile()
      return [f]
    }
    return []
  }
  if (entry.isDirectory) {
    report.onDir()
    const children = await readAllEntries(entry)
    const nested = await Promise.all(children.map((c) => walkEntry(c, report)))
    return nested.flat()
  }
  return []
}

interface ScanState {
  active: boolean
  filesSeen: number
  dirsSeen: number
}

export function FileDropzone() {
  const addFiles = useUploadStore((s) => s.addFiles)
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  // Folder scanning is synchronous from the user's view (their click is
  // blocked until walk finishes), so we surface a live counter while it runs.
  const [scan, setScan] = useState<ScanState>({ active: false, filesSeen: 0, dirsSeen: 0 })

  const makeReporter = (): ScanReporter => {
    // Throttle setState to once per ~30 entries to avoid render storms when
    // walking 10k+ files.
    let pendingFiles = 0
    let pendingDirs = 0
    let lastFlush = performance.now()
    const flush = () => {
      const f = pendingFiles
      const d = pendingDirs
      pendingFiles = 0
      pendingDirs = 0
      setScan((s) => ({
        active: true,
        filesSeen: s.filesSeen + f,
        dirsSeen: s.dirsSeen + d,
      }))
      lastFlush = performance.now()
    }
    return {
      onFile() {
        pendingFiles += 1
        if (pendingFiles + pendingDirs >= 32 || performance.now() - lastFlush > 100) flush()
      },
      onDir() {
        pendingDirs += 1
        if (pendingFiles + pendingDirs >= 32 || performance.now() - lastFlush > 100) flush()
      },
    }
  }

  const onDrop = async (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setOver(false)
    const items = e.dataTransfer.items
    if (items && items.length) {
      const entries: FsEntry[] = []
      for (let i = 0; i < items.length; i++) {
        // webkitGetAsEntry is the production-supported name across Chrome/Edge/Safari/Firefox.
        const entry = (items[i] as unknown as {
          webkitGetAsEntry?: () => FsEntry | null
        }).webkitGetAsEntry?.()
        if (entry) entries.push(entry)
      }
      if (entries.length) {
        setScan({ active: true, filesSeen: 0, dirsSeen: 0 })
        try {
          const reporter = makeReporter()
          const collected = (
            await Promise.all(entries.map((en) => walkEntry(en, reporter)))
          ).flat()
          addFiles(collected)
        } finally {
          setScan({ active: false, filesSeen: 0, dirsSeen: 0 })
        }
        return
      }
    }
    // Fallback: no items API available — take whatever is in .files (loose files only).
    const files = Array.from(e.dataTransfer.files ?? []).filter((f) => isImage(f.name))
    addFiles(files)
  }

  const onPickerChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    // Read length only — do NOT call Array.from() yet. For folders with tens
    // of thousands of files, materializing the FileList into a plain array
    // can block the main thread for seconds, which is exactly the freeze
    // users see between picker close and the Scanning… indicator.
    const list = e.target.files as FileList | File[] | null
    const total = list?.length ?? 0
    if (!list || total === 0) return

    // Surface the indicator synchronously so React schedules a paint, then
    // yield once before doing any per-file work.
    setScan({ active: true, filesSeen: 0, dirsSeen: 0 })
    await new Promise((r) => setTimeout(r, 0))

    try {
      const filtered: File[] = []
      for (let i = 0; i < total; i++) {
        const f = list[i] as File
        if (isImage(f.name)) filtered.push(f)
        // Yield + paint progress every 200 entries so the UI stays alive
        // even on 50k-file folders.
        if (i % 200 === 0) {
          setScan({ active: true, filesSeen: filtered.length, dirsSeen: 0 })
          // eslint-disable-next-line no-await-in-loop
          await new Promise((r) => setTimeout(r, 0))
        }
      }
      addFiles(filtered)
    } finally {
      e.target.value = ''
      setScan({ active: false, filesSeen: 0, dirsSeen: 0 })
    }
  }

  const scanLabel = scan.active
    ? scan.dirsSeen > 0
      ? `Scanning folder… ${scan.filesSeen} images, ${scan.dirsSeen} directories so far`
      : `Scanning folder… ${scan.filesSeen} images so far`
    : null

  return (
    <div
      data-testid="file-dropzone"
      onDragEnter={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      onClick={() => {
        if (!scan.active) inputRef.current?.click()
      }}
      style={{
        border: `2px dashed ${over ? '#4f46e5' : '#9ca3af'}`,
        borderRadius: 6,
        padding: 24,
        textAlign: 'center',
        cursor: scan.active ? 'progress' : 'pointer',
        background: scan.active ? '#fef3c7' : over ? '#eef2ff' : '#fafafa',
      }}
    >
      {scan.active ? (
        <span data-testid="file-dropzone-scanning">{scanLabel}</span>
      ) : (
        <>Drop a folder of images here, or click to pick a folder</>
      )}
      <input
        ref={inputRef}
        data-testid="file-dropzone-input"
        type="file"
        multiple
        // webkitdirectory enables folder picking in Chrome/Edge/Safari/Firefox.
        // React types don't include it; lower-case attr survives DOM serialization.
        {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
        style={{ display: 'none' }}
        onChange={onPickerChange}
      />
    </div>
  )
}
