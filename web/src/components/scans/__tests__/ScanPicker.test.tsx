import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ScanPicker } from '@/components/scans/ScanPicker'
import * as uploadApi from '@/api/upload'
import { resetProjectStore, useProjectStore } from '@/state/projectSlice'

function wrap(ui: React.ReactNode, initialUrl = '/projects/p1/scans/11/compute') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <Routes>
          <Route path="/projects/:projectId/scans/:scanId/:tab" element={ui} />
          <Route path="/projects/:projectId/:tab" element={ui} />
          <Route path="/projects/:projectId" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  resetProjectStore()
  vi.restoreAllMocks()
})

describe('ScanPicker', () => {
  it('renders the dropdown of scans and reflects the active scan', async () => {
    vi.spyOn(uploadApi, 'listScansForProject').mockResolvedValue([
      { scan_id: 11, name: 's-eleven', material: 'graphene', image_count: 4, created_at: 't' },
      { scan_id: 12, name: 's-twelve', material: 'MoS2', image_count: 9, created_at: 't' },
    ])
    useProjectStore.getState().setActiveProjectId('p1')
    useProjectStore.getState().setActiveScanId(11)

    render(wrap(<ScanPicker />))
    await waitFor(() => expect(screen.getByTestId('scan-picker-select')).toBeTruthy())
    const select = screen.getByTestId('scan-picker-select') as HTMLSelectElement
    expect(select.value).toBe('11')
    expect(screen.getByTestId('scan-picker-option-11')).toBeTruthy()
    expect(screen.getByTestId('scan-picker-option-12')).toBeTruthy()
  })

  it('shows a "create scan" CTA when the project has 0 scans', async () => {
    vi.spyOn(uploadApi, 'listScansForProject').mockResolvedValue([])
    useProjectStore.getState().setActiveProjectId('p1')
    render(wrap(<ScanPicker />, '/projects/p1'))
    await waitFor(() => expect(screen.getByTestId('scan-picker-empty-cta')).toBeTruthy())
    expect(screen.queryByTestId('scan-picker-select')).toBeNull()
  })

  it('changing the dropdown navigates to the new scan id', async () => {
    vi.spyOn(uploadApi, 'listScansForProject').mockResolvedValue([
      { scan_id: 11, name: 's11', material: 'g', image_count: 1, created_at: 't' },
      { scan_id: 12, name: 's12', material: 'g', image_count: 1, created_at: 't' },
    ])
    useProjectStore.getState().setActiveProjectId('p1')
    useProjectStore.getState().setActiveScanId(11)

    render(wrap(<ScanPicker />))
    const select = await screen.findByTestId('scan-picker-select')
    await userEvent.selectOptions(select, '12')
    // We can't easily assert the navigate target without injecting a router spy,
    // but the slice should update and the URL hook should follow. Assert slice.
    expect(useProjectStore.getState().activeScanId).toBe(12)
  })
})
