// web/src/components/upload/ProgressList.tsx
import { useUploadStore } from '@/state/uploadSlice'
import { FileRow } from './FileRow'

export function ProgressList() {
  const order = useUploadStore((s) => s.order)
  if (order.length === 0) {
    return (
      <p data-testid="progress-list-empty" style={{ color: '#6b7280' }}>
        No files queued.
      </p>
    )
  }
  return (
    <div data-testid="progress-list" style={{ marginTop: 8 }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 60px 60px 100px 80px 60px',
          gap: 8,
          fontSize: 11,
          color: '#6b7280',
          padding: '4px 0',
        }}
      >
        <span>filename</span>
        <span>ix</span>
        <span>iy</span>
        <span>status</span>
        <span>progress</span>
        <span></span>
      </div>
      {order.map((uid) => (
        <FileRow key={uid} uid={uid} />
      ))}
    </div>
  )
}
