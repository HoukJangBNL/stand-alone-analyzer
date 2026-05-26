import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ScanTable } from '@/components/scans/ScanTable'
import * as uploadApi from '@/api/upload'

vi.mock('@/api/upload', async () => {
  const actual = await vi.importActual<typeof uploadApi>('@/api/upload')
  return { ...actual, listScansForProject: vi.fn(), deleteScan: vi.fn() }
})

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

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

describe('ScanTable sort', () => {
  beforeEach(() => {
    vi.mocked(uploadApi.listScansForProject).mockResolvedValue([
      {
        scan_id: 10, name: 'charlie', material: 'WSe2',
        image_count: 200, uploaded_count: 200, status: 'ready',
        created_at: '2026-05-03T10:00:00Z',
      },
      {
        scan_id: 11, name: 'alpha', material: 'graphene',
        image_count: 100, uploaded_count: 100, status: 'ready',
        created_at: '2026-05-01T10:00:00Z',
      },
      {
        scan_id: 12, name: 'beta', material: 'MoS2',
        image_count: 50, uploaded_count: 30, status: 'draft',
        created_at: '2026-05-02T10:00:00Z',
      },
    ])
  })

  it('sorts by name ascending then descending on header click', async () => {
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')

    let rows = screen.getAllByTestId(/^scan-table-row-/)
    expect(rows[0].getAttribute('data-testid')).toBe('scan-table-row-10')

    fireEvent.click(screen.getByTestId('scan-table-col-name'))
    rows = screen.getAllByTestId(/^scan-table-row-/)
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual([
      'scan-table-row-11',
      'scan-table-row-12',
      'scan-table-row-10',
    ])

    fireEvent.click(screen.getByTestId('scan-table-col-name'))
    rows = screen.getAllByTestId(/^scan-table-row-/)
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual([
      'scan-table-row-10',
      'scan-table-row-12',
      'scan-table-row-11',
    ])
  })

  it('sorts by images uploaded count', async () => {
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')
    fireEvent.click(screen.getByTestId('scan-table-col-images'))
    const rows = screen.getAllByTestId(/^scan-table-row-/)
    expect(rows.map((r) => r.getAttribute('data-testid'))).toEqual([
      'scan-table-row-12', // 30
      'scan-table-row-11', // 100
      'scan-table-row-10', // 200
    ])
  })
})

describe('ScanTable delete', () => {
  beforeEach(() => {
    vi.mocked(uploadApi.deleteScan).mockReset()
    vi.mocked(uploadApi.listScansForProject).mockResolvedValue([
      {
        scan_id: 1, name: 'alpha', material: 'graphene',
        image_count: 100, uploaded_count: 100, status: 'ready',
        created_at: '2026-05-01T10:00:00Z',
      },
    ])
    vi.mocked(uploadApi.deleteScan).mockResolvedValue(undefined)
  })

  it('shows delete button per row, opens confirm, calls deleteScan', async () => {
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')

    const delBtn = screen.getByTestId('scan-table-delete-1')
    expect(delBtn).toBeTruthy()

    fireEvent.click(delBtn)

    const confirm = await screen.findByTestId('scan-table-confirm-1')
    expect(confirm.textContent).toContain('alpha')

    fireEvent.click(screen.getByTestId('scan-table-confirm-yes-1'))

    await waitFor(() => {
      expect(uploadApi.deleteScan).toHaveBeenCalledWith(1)
    })
  })

  it('cancel keeps scan and closes dialog', async () => {
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')
    fireEvent.click(screen.getByTestId('scan-table-delete-1'))
    await screen.findByTestId('scan-table-confirm-1')
    fireEvent.click(screen.getByTestId('scan-table-confirm-no-1'))

    await waitFor(() => {
      expect(screen.queryByTestId('scan-table-confirm-1')).toBeNull()
    })
    expect(uploadApi.deleteScan).not.toHaveBeenCalled()
  })

  it('keeps row on delete failure', async () => {
    vi.mocked(uploadApi.deleteScan).mockRejectedValue(
      new Error('deleteScan failed: forbidden (scan_edit)')
    )
    wrap(<ScanTable />)
    await screen.findByTestId('scan-table')
    fireEvent.click(screen.getByTestId('scan-table-delete-1'))
    await screen.findByTestId('scan-table-confirm-1')
    fireEvent.click(screen.getByTestId('scan-table-confirm-yes-1'))

    await waitFor(() => {
      expect(uploadApi.deleteScan).toHaveBeenCalledWith(1)
    })
    expect(screen.getByTestId('scan-table-row-1')).toBeTruthy()
  })
})
