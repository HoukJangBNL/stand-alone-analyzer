// web/src/components/upload/FileRow.tsx
import { useUploadStore, type UploadFile } from '@/state/uploadSlice'
import { getOrchestrator } from '@/lib/uploadOrchestrator'

interface Props {
  uid: string
}

const STATUS_COLORS: Record<UploadFile['status'], string> = {
  queued: '#9ca3af',
  hashing: '#fbbf24',
  presigning: '#fbbf24',
  uploading: '#3b82f6',
  completing: '#3b82f6',
  done: '#10b981',
  failed: '#ef4444',
}

export function FileRow({ uid }: Props) {
  const file = useUploadStore((s) => s.files[uid])
  const setGrid = useUploadStore((s) => s.setGrid)
  const removeFile = useUploadStore((s) => s.removeFile)
  if (!file) return null

  const onRetry = () => {
    void getOrchestrator().retry(uid)
  }

  return (
    <div
      data-testid={`file-row-${uid}`}
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 60px 60px 100px 80px 60px',
        gap: 8,
        alignItems: 'center',
        padding: '4px 0',
        borderBottom: '1px solid #f0f0f0',
      }}
    >
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {file.filename}
      </span>
      <input
        data-testid={`file-row-${uid}-ix`}
        type="number"
        min={0}
        value={file.grid_ix ?? ''}
        onChange={(e) =>
          setGrid(uid, e.target.value === '' ? null : Number(e.target.value), file.grid_iy)
        }
        disabled={file.status !== 'queued' && file.status !== 'failed'}
        placeholder="ix"
      />
      <input
        data-testid={`file-row-${uid}-iy`}
        type="number"
        min={0}
        value={file.grid_iy ?? ''}
        onChange={(e) =>
          setGrid(uid, file.grid_ix, e.target.value === '' ? null : Number(e.target.value))
        }
        disabled={file.status !== 'queued' && file.status !== 'failed'}
        placeholder="iy"
      />
      <span
        data-testid={`file-row-${uid}-status`}
        style={{ color: STATUS_COLORS[file.status], fontSize: 12 }}
      >
        {file.status}
        {file.error ? `: ${file.error}` : ''}
        {file.error && file.request_id ? (
          <>
            {' '}
            <small data-testid={`file-row-reqid-${uid}`} style={{ color: '#6b7280' }}>
              req-id: {file.request_id}
            </small>
          </>
        ) : null}
      </span>
      <div style={{ width: 80, height: 6, background: '#e5e7eb', borderRadius: 3 }}>
        <div
          data-testid={`file-row-${uid}-progress`}
          style={{
            width: `${Math.round(file.progress * 100)}%`,
            height: '100%',
            background: STATUS_COLORS[file.status],
            borderRadius: 3,
          }}
        />
      </div>
      {file.status === 'failed' ? (
        <button data-testid={`file-row-${uid}-retry`} onClick={onRetry}>
          retry
        </button>
      ) : file.status === 'queued' ? (
        <button data-testid={`file-row-${uid}-remove`} onClick={() => removeFile(uid)}>
          ×
        </button>
      ) : (
        <span />
      )}
    </div>
  )
}
