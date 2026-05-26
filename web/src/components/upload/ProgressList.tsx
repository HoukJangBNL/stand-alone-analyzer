// web/src/components/upload/ProgressList.tsx
import { useState } from 'react'
import { useUploadStore } from '@/state/uploadSlice'
import { FileRow } from './FileRow'

// Showing every dropped file as a row blocks the main thread when users
// drop a 5k-image folder. Cap the visible list and surface a summary tail.
const MAX_VISIBLE_ROWS = 200

export function ProgressList() {
  const order = useUploadStore((s) => s.order)
  const files = useUploadStore((s) => s.files)
  const [failedOnly, setFailedOnly] = useState(false)

  if (order.length === 0) {
    return (
      <p data-testid="progress-list-empty" style={{ color: '#6b7280' }}>
        No files queued.
      </p>
    )
  }

  const filtered = failedOnly
    ? order.filter((uid) => files[uid]?.status === 'failed')
    : order
  const visible = filtered.slice(0, MAX_VISIBLE_ROWS)
  const hidden = filtered.length - visible.length
  return (
    <div
      data-testid="progress-list"
      style={{
        marginTop: 8,
        maxHeight: 280,
        overflowY: 'auto',
        border: '1px solid #e5e7eb',
        borderRadius: 4,
        padding: 4,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'flex-end',
          padding: '4px 0',
          position: 'sticky',
          top: 0,
          background: 'white',
          zIndex: 2,
        }}
      >
        <button
          data-testid="progress-list-failed-only"
          onClick={() => setFailedOnly((v) => !v)}
          style={{ fontSize: 11 }}
        >
          {failedOnly ? 'Show all' : 'Show failed only'}
        </button>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 60px 60px 100px 80px 60px',
          gap: 8,
          fontSize: 11,
          color: '#6b7280',
          padding: '4px 0',
          position: 'sticky',
          top: 28,
          background: 'white',
          zIndex: 1,
        }}
      >
        <span>filename</span>
        <span>ix</span>
        <span>iy</span>
        <span>status</span>
        <span>progress</span>
        <span></span>
      </div>
      {visible.map((uid) => (
        <FileRow key={uid} uid={uid} />
      ))}
      {hidden > 0 && (
        <p
          data-testid="progress-list-truncated"
          style={{ color: '#6b7280', fontSize: 12, padding: '4px 0' }}
        >
          …and {hidden} more file{hidden === 1 ? '' : 's'} (queued; uploads run in
          parallel regardless of this list).
        </p>
      )}
    </div>
  )
}
