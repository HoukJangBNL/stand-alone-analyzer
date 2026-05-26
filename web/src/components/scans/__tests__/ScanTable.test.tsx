import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ScanTable } from '@/components/scans/ScanTable'
import * as uploadApi from '@/api/upload'

vi.mock('@/api/upload', async () => {
  const actual = await vi.importActual<typeof uploadApi>('@/api/upload')
  return { ...actual, listScansForProject: vi.fn(), deleteScan: vi.fn() }
})

function wrap(node: React.ReactNode, { pid = 'pid-1', sid = '' } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const initial = sid
    ? `/projects/${pid}/scans/${sid}/compute`
    : `/projects/${pid}`
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/projects/:projectId/scans/:scanId/:tab" element={node} />
          <Route path="/projects/:projectId" element={node} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('ScanTable', () => {
  beforeEach(() => {
    vi.mocked(uploadApi.listScansForProject).mockResolvedValue([
      {
        scan_id: 1, name: 'alpha', material: 'graphene',
        image_count: 100, uploaded_count: 100, status: 'ready',
        created_at: '2026-05-01T10:00:00Z',
      },
      {
        scan_id: 2, name: 'beta', material: 'MoS2',
        image_count: 50, uploaded_count: 30, status: 'draft',
        created_at: '2026-05-02T10:00:00Z',
      },
    ])
  })

  it('renders one row per scan with all 6 columns', async () => {
    wrap(<ScanTable />)
    expect(await screen.findByTestId('scan-table')).toBeTruthy()
    expect(screen.getByTestId('scan-table-row-1')).toBeTruthy()
    expect(screen.getByTestId('scan-table-row-2')).toBeTruthy()

    expect(screen.getByTestId('scan-table-col-name').textContent).toMatch(/name/i)
    expect(screen.getByTestId('scan-table-col-material').textContent).toMatch(/material/i)
    expect(screen.getByTestId('scan-table-col-images').textContent).toMatch(/images/i)
    expect(screen.getByTestId('scan-table-col-status').textContent).toMatch(/status/i)
    expect(screen.getByTestId('scan-table-col-created').textContent).toMatch(/created/i)
    expect(screen.getByTestId('scan-table-col-actions').textContent).toMatch(/actions/i)

    expect(screen.getByTestId('scan-table-cell-1-name').textContent).toContain('alpha')
    expect(screen.getByTestId('scan-table-cell-1-material').textContent).toContain('graphene')
    expect(screen.getByTestId('scan-table-cell-1-images').textContent).toContain('100/100')
    expect(screen.getByTestId('scan-table-cell-1-status').textContent).toContain('ready')
  })

  it('shows "No scans" empty state when list is empty', async () => {
    vi.mocked(uploadApi.listScansForProject).mockResolvedValue([])
    wrap(<ScanTable />)
    expect(await screen.findByTestId('scan-table-empty')).toBeTruthy()
  })
})
