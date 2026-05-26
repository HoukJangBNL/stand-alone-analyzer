// web/src/components/scans/ScanTable.tsx
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { listScansForProject, type ScanSummary } from '@/api/upload'
import { useProjectStore } from '@/state/projectSlice'
import { UploadModal } from '@/components/upload/UploadModal'

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

  const scans = useQuery<ScanSummary[]>({
    queryKey: ['scans', 'list', projectId],
    queryFn: () => listScansForProject(projectId!),
    enabled: !!projectId,
    staleTime: 5_000,
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
            <th data-testid="scan-table-col-name" style={TH}>Name</th>
            <th data-testid="scan-table-col-material" style={TH}>Material</th>
            <th data-testid="scan-table-col-images" style={TH}>Images</th>
            <th data-testid="scan-table-col-status" style={TH}>Status</th>
            <th data-testid="scan-table-col-created" style={TH}>Created</th>
            <th data-testid="scan-table-col-actions" style={TH}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => {
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
                  {/* delete button added in Task 8 */}
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
