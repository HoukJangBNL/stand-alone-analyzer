// web/src/components/upload/FileDropzone.tsx
import { useRef, useState, type DragEvent } from 'react'
import { useUploadStore } from '@/state/uploadSlice'

export function FileDropzone() {
  const addFiles = useUploadStore((s) => s.addFiles)
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setOver(false)
    const files = Array.from(e.dataTransfer.files ?? [])
    if (files.length) addFiles(files)
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
      Drop image files here, or click to pick
      <input
        ref={inputRef}
        data-testid="file-dropzone-input"
        type="file"
        multiple
        style={{ display: 'none' }}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          if (files.length) addFiles(files)
          e.target.value = ''
        }}
      />
    </div>
  )
}
