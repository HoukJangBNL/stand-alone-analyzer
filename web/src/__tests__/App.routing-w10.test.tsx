import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useProjectStore, resetProjectStore } from '@/state/projectSlice'
import * as uploadApi from '@/api/upload'
import { ProjectScanSync } from '@/App'

function ProjectScanSyncHarness() {
  return <ProjectScanSync />
}

function withRouter(initial: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>
        <Routes>
          <Route path="/projects/:projectId/scans/:scanId/:tab" element={<ProjectScanSyncHarness />} />
          <Route path="/projects/:projectId/:tab" element={<ProjectScanSyncHarness />} />
          <Route path="/projects/:projectId" element={<ProjectScanSyncHarness />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  resetProjectStore()
  vi.restoreAllMocks()
  vi.spyOn(uploadApi, 'listScansForProject').mockResolvedValue([])
})

describe('ProjectScanSync (W10-D)', () => {
  it('hydrates slice from /projects/:pid/scans/:sid URL', async () => {
    render(withRouter('/projects/p1/scans/11/compute'))
    await waitFor(() => {
      expect(useProjectStore.getState().activeProjectId).toBe('p1')
      expect(useProjectStore.getState().activeScanId).toBe(11)
    })
  })

  it('clears activeScanId on /projects/:pid (no scan in URL)', async () => {
    useProjectStore.getState().setActiveScanId(99)
    render(withRouter('/projects/p1'))
    await waitFor(() => {
      expect(useProjectStore.getState().activeProjectId).toBe('p1')
      expect(useProjectStore.getState().activeScanId).toBeNull()
    })
  })

  it('legacy /projects/:pid/<tab> hydrates project but clears scan (forces picker)', async () => {
    useProjectStore.getState().setActiveScanId(99)
    render(withRouter('/projects/p1/compute'))
    await waitFor(() => {
      expect(useProjectStore.getState().activeProjectId).toBe('p1')
      expect(useProjectStore.getState().activeScanId).toBeNull()
    })
  })
})
