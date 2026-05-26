// web/src/components/scans/ScanPicker.tsx
import { useState, useMemo } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { listScansForProject, type ScanSummary } from '@/api/upload'
import { useProjectStore } from '@/state/projectSlice'
import { UploadModal } from '@/components/upload/UploadModal'

export function ScanPicker() {
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

  const activeSid = useMemo(() => (urlSid ? Number(urlSid) : null), [urlSid])
  const tabSlug = tab ?? 'compute'

  if (!projectId) return null

  if (scans.isLoading) {
    return <div data-testid="scan-picker-loading" style={{ fontSize: 12, color: '#6b7280' }}>Loading scans…</div>
  }

  if ((scans.data ?? []).length === 0) {
    return (
      <div
        data-testid="scan-picker-empty"
        style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '6px 0' }}
      >
        <span style={{ fontSize: 12, color: '#6b7280' }}>No scans in this project yet.</span>
        <button
          data-testid="scan-picker-empty-cta"
          type="button"
          onClick={() => setShowUpload(true)}
        >
          + New scan
        </button>
        <UploadModal projectId={projectId} open={showUpload} onClose={() => setShowUpload(false)} />
      </div>
    )
  }

  const onChange = (sidStr: string) => {
    const sid = Number(sidStr)
    setActiveScan(sid)
    navigate(`/projects/${projectId}/scans/${sid}/${tabSlug}`)
  }

  return (
    <div
      data-testid="scan-picker"
      style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '6px 0' }}
    >
      <label style={{ fontSize: 12, color: '#6b7280' }} htmlFor="scan-picker-select">Scan</label>
      <select
        id="scan-picker-select"
        data-testid="scan-picker-select"
        value={activeSid ?? ''}
        onChange={(e) => onChange(e.target.value)}
      >
        {!activeSid && <option value="" disabled>Select scan…</option>}
        {(scans.data ?? []).map((s) => (
          <option
            key={s.scan_id}
            value={String(s.scan_id)}
            data-testid={`scan-picker-option-${s.scan_id}`}
          >
            {s.name} ({s.image_count} imgs)
          </option>
        ))}
      </select>
      <button
        data-testid="scan-picker-new"
        type="button"
        onClick={() => setShowUpload(true)}
      >
        + New scan
      </button>
      <UploadModal projectId={projectId} open={showUpload} onClose={() => setShowUpload(false)} />
    </div>
  )
}
