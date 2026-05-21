// web/src/components/__tests__/Sidebar.test.tsx
import React from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { Sidebar } from '@/components/Sidebar'
import { resetProjectStore, useProjectStore } from '@/state/projectSlice'

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('<Sidebar>', () => {
  beforeEach(() => {
    resetProjectStore()
    vi.unstubAllGlobals()
  })

  it('renders project list from /api/v1/projects', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            projects: [
              { project_id: 'p1', analysis_folder: '/a' },
              { project_id: 'p2', analysis_folder: '/b' },
            ],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    render(wrap(<Sidebar />))
    expect(await screen.findByTestId('sidebar-project-list')).not.toBeNull()
    expect(await screen.findByTestId('sidebar-project-row-p1')).not.toBeNull()
    expect(await screen.findByTestId('sidebar-project-row-p2')).not.toBeNull()
  })

  it('clicking a project sets activeProjectId in the slice', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ projects: [{ project_id: 'p1', analysis_folder: '/a' }] }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
    )
    render(wrap(<Sidebar />))
    const row = await screen.findByTestId('sidebar-project-select-p1')
    fireEvent.click(row)
    await waitFor(() =>
      expect(useProjectStore.getState().activeProjectId).toBe('p1')
    )
  })

  it('shows the create form when create button is clicked', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ projects: [] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    )
    render(wrap(<Sidebar />))
    fireEvent.click(await screen.findByTestId('sidebar-create-toggle'))
    expect(await screen.findByTestId('sidebar-create-form')).not.toBeNull()
  })
})
