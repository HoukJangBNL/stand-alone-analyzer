// web/src/components/scans/ScanTable.tsx
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { listScansForProject, deleteScan, type ScanSummary } from '@/api/upload'
import { useProjectStore } from '@/state/projectSlice'
import { UploadModal } from '@/components/upload/UploadModal'

type SortKey = 'name' | 'material' | 'images' | 'status' | 'created'
type SortDir = 'asc' | 'desc'

const TH: React.CSSProperties = {
  textAlign: 'left',
  fontSize: 12,
  color: '#374151',
  padding: '4px 8px',
  borderBottom: '1px solid #e5e7eb',
  cursor: 'pointer',
  userSelect: 'none',
}

const TD: React.CSSProperties = {
  fontSize: 12,
  padding: '4px 8px',
  borderBottom: '1px solid #f3f4f6',
}

export function ScanTable() {
  const navigate = useNavigate()
  const { projectId: urlPid, scanId: urlSid, tab } = useParams<{
    projectId: string
    scanId?: string
    tab?: string
  }>()
  const sliceProject = useProjectStore((s) => s.activeProjectId)
  const setActiveScan = useProjectStore((s) => s.setActiveScanId)
  const projectId = urlPid ?? sliceProject ?? null
  const [showUpload, setShowUpload] = useState(false)
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [confirmId, setConfirmId] = useState<number | null>(null)

  const qc = useQueryClient()
  const scans = useQuery<ScanSummary[]>({
    queryKey: ['scans', 'list', projectId],
    queryFn: () => listScansForProject(projectId!),
    enabled: !!projectId,
    staleTime: 5_000,
  })

  const del = useMutation({
    mutationFn: (sid: number) => deleteScan(sid),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scans', 'list', projectId] })
      toast.success('Scan deleted')
      setConfirmId(null)
    },
    onError: (e: Error) => {
      toast.error(e.message ?? 'Delete failed')
      setConfirmId(null)
    },
  })

  if (!projectId) return null

  const tabSlug = tab ?? 'compute'
  const activeSid = urlSid ? Number(urlSid) : null

  if (scans.isLoading) {
    return <div data-testid="scan-table-loading" style={{ fontSize: 12, color: '#6b7280' }}>Loading scans…</div>
  }

  const rows = scans.data ?? []

  if (rows.length === 0) {
    return (
      <div data-testid="scan-table-empty" style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '6px 0' }}>
        <span style={{ fontSize: 12, color: '#6b7280' }}>No scans in this project yet.</span>
        <button
          data-testid="scan-table-empty-cta"
          type="button"
          onClick={() => setShowUpload(true)}
        >
          + New scan
        </button>
        <UploadModal projectId={projectId} open={showUpload} onClose={() => setShowUpload(false)} />
      </div>
    )
  }

  const onSelect = (sid: number) => {
    setActiveScan(sid)
    navigate(`/projects/${projectId}/scans/${sid}/${tabSlug}`)
  }

  const onSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sorted = (() => {
    if (!sortKey) return rows
    const cmp = (a: ScanSummary, b: ScanSummary): number => {
      switch (sortKey) {
        case 'name': return a.name.localeCompare(b.name)
        case 'material': return a.material.localeCompare(b.material)
        case 'images': return a.uploaded_count - b.uploaded_count
        case 'status': return a.status.localeCompare(b.status)
        case 'created': return Date.parse(a.created_at) - Date.parse(b.created_at)
      }
    }
    const out = [...rows].sort(cmp)
    return sortDir === 'asc' ? out : out.reverse()
  })()

  return (
    <div data-testid="scan-table" style={{ padding: '6px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 4 }}>
        <button
          data-testid="scan-table-new"
          type="button"
          onClick={() => setShowUpload(true)}
        >
          + New scan
        </button>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th data-testid="scan-table-col-name" style={TH} onClick={() => onSort('name')}>
              Name{sortKey === 'name' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
            </th>
            <th data-testid="scan-table-col-material" style={TH} onClick={() => onSort('material')}>
              Material{sortKey === 'material' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
            </th>
            <th data-testid="scan-table-col-images" style={TH} onClick={() => onSort('images')}>
              Images{sortKey === 'images' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
            </th>
            <th data-testid="scan-table-col-status" style={TH} onClick={() => onSort('status')}>
              Status{sortKey === 'status' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
            </th>
            <th data-testid="scan-table-col-created" style={TH} onClick={() => onSort('created')}>
              Created{sortKey === 'created' ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
            </th>
            <th data-testid="scan-table-col-actions" style={{ ...TH, cursor: 'default' }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => {
            const isActive = s.scan_id === activeSid
            return (
              <tr
                key={s.scan_id}
                data-testid={`scan-table-row-${s.scan_id}`}
                style={{ background: isActive ? '#eef2ff' : undefined, cursor: 'pointer' }}
                onClick={() => onSelect(s.scan_id)}
              >
                <td data-testid={`scan-table-cell-${s.scan_id}-name`} style={TD}>{s.name}</td>
                <td data-testid={`scan-table-cell-${s.scan_id}-material`} style={TD}>{s.material}</td>
                <td data-testid={`scan-table-cell-${s.scan_id}-images`} style={TD}>
                  {s.uploaded_count}/{s.image_count}
                </td>
                <td data-testid={`scan-table-cell-${s.scan_id}-status`} style={TD}>{s.status}</td>
                <td data-testid={`scan-table-cell-${s.scan_id}-created`} style={TD}>
                  {new Date(s.created_at).toLocaleString()}
                </td>
                <td data-testid={`scan-table-cell-${s.scan_id}-actions`} style={TD}>
                  <button
                    data-testid={`scan-table-delete-${s.scan_id}`}
                    type="button"
                    onClick={(ev) => {
                      ev.stopPropagation()
                      setConfirmId(s.scan_id)
                    }}
                    style={{ fontSize: 11, color: '#b91c1c' }}
                  >
                    Delete
                  </button>
                  {confirmId === s.scan_id && (
                    <div
                      data-testid={`scan-table-confirm-${s.scan_id}`}
                      onClick={(ev) => ev.stopPropagation()}
                      style={{
                        position: 'absolute',
                        marginTop: 4,
                        padding: 8,
                        background: '#fff',
                        border: '1px solid #b91c1c',
                        borderRadius: 4,
                        zIndex: 10,
                        fontSize: 12,
                      }}
                    >
                      Delete scan "{s.name}"? This wipes its DB row and all S3 objects.
                      <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                        <button
                          data-testid={`scan-table-confirm-yes-${s.scan_id}`}
                          type="button"
                          disabled={del.isPending}
                          onClick={() => del.mutate(s.scan_id)}
                          style={{ color: '#b91c1c' }}
                        >
                          {del.isPending ? 'Deleting…' : 'Yes, delete'}
                        </button>
                        <button
                          data-testid={`scan-table-confirm-no-${s.scan_id}`}
                          type="button"
                          onClick={() => setConfirmId(null)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      <UploadModal projectId={projectId} open={showUpload} onClose={() => setShowUpload(false)} />
    </div>
  )
}
