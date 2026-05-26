import { describe, it, expect, vi, beforeEach } from 'vitest'
import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Sidebar } from '@/components/Sidebar'
import * as projectsApi from '@/api/projects'
import { resetProjectStore, useProjectStore } from '@/state/projectSlice'

function wrap(ui: React.ReactNode, initial = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[initial]}>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

beforeEach(() => {
  resetProjectStore()
  vi.restoreAllMocks()
})

describe('Sidebar (W10-D)', () => {
  it('renders the project list', async () => {
    vi.spyOn(projectsApi, 'listProjects').mockResolvedValue([
      { project_id: 'p1', name: 'demo', description: null, created_at: 't', scan_count: 2 },
      { project_id: 'p2', name: 'other', description: null, created_at: 't', scan_count: 0 },
    ])
    render(wrap(<Sidebar />))
    await waitFor(() => expect(screen.getByTestId('sidebar-project-row-p1')).toBeTruthy())
    expect(screen.getByTestId('sidebar-project-row-p2')).toBeTruthy()
  })

  it('shows the empty-state with only "+ new project" enabled when 0 projects exist', async () => {
    vi.spyOn(projectsApi, 'listProjects').mockResolvedValue([])
    render(wrap(<Sidebar />))
    await waitFor(() => expect(screen.getByTestId('sidebar-empty-state')).toBeTruthy())
    expect(screen.getByTestId('sidebar-new-project-btn')).toBeTruthy()
  })

  it('opens the CreateProjectModal when clicking the + button', async () => {
    vi.spyOn(projectsApi, 'listProjects').mockResolvedValue([])
    render(wrap(<Sidebar />))
    await userEvent.click(await screen.findByTestId('sidebar-new-project-btn'))
    expect(screen.getByTestId('create-project-modal')).toBeTruthy()
  })

  it('after creating a project, syncs slice to the new project_id', async () => {
    vi.spyOn(projectsApi, 'listProjects').mockResolvedValue([])
    vi.spyOn(projectsApi, 'createProject').mockResolvedValue({
      project_id: 'p_new', name: 'fresh', description: null,
      created_at: 't', scan_count: 0,
    })
    render(wrap(<Sidebar />))
    await userEvent.click(await screen.findByTestId('sidebar-new-project-btn'))
    await userEvent.type(screen.getByTestId('create-project-modal-name'), 'fresh')
    await userEvent.click(screen.getByTestId('create-project-modal-submit'))
    await waitFor(() => expect(useProjectStore.getState().activeProjectId).toBe('p_new'))
  })
})
