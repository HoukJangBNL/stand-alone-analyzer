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

async function walkEntry(entry: FsEntry): Promise<File[]> {
  if (entry.isFile) {
    const f = await entryToFile(entry)
    return f && isImage(f.name) ? [f] : []
  }
  if (entry.isDirectory) {
    const children = await readAllEntries(entry)
    const nested = await Promise.all(children.map(walkEntry))
    return nested.flat()
  }
  return []
}

export function FileDropzone() {
  const addFiles = useUploadStore((s) => s.addFiles)
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

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
        const collected = (await Promise.all(entries.map(walkEntry))).flat()
        addFiles(collected)
        return
      }
    }
    // Fallback: no items API available — take whatever is in .files (loose files only).
    const files = Array.from(e.dataTransfer.files ?? []).filter((f) => isImage(f.name))
    addFiles(files)
  }

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
      onClick={() => inputRef.current?.click()}
      style={{
        border: `2px dashed ${over ? '#4f46e5' : '#9ca3af'}`,
        borderRadius: 6,
        padding: 24,
        textAlign: 'center',
        cursor: 'pointer',
        background: over ? '#eef2ff' : '#fafafa',
      }}
    >
      Drop a folder of images here, or click to pick a folder
      <input
        ref={inputRef}
        data-testid="file-dropzone-input"
        type="file"
        multiple
        // webkitdirectory enables folder picking in Chrome/Edge/Safari/Firefox.
        // React types don't include it; lower-case attr survives DOM serialization.
        {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
        style={{ display: 'none' }}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []).filter((f) => isImage(f.name))
          addFiles(files)
          e.target.value = ''
        }}
      />
    </div>
  )
}
