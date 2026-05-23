import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { CreateProjectModal } from '@/components/projects/CreateProjectModal'
import * as projectsApi from '@/api/projects'

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('CreateProjectModal', () => {
  it('renders nothing when open=false', () => {
    render(wrap(<CreateProjectModal open={false} onClose={() => {}} onCreated={() => {}} />))
    expect(screen.queryByTestId('create-project-modal')).toBeNull()
  })

  it('disables submit while name is empty', async () => {
    render(wrap(<CreateProjectModal open onClose={() => {}} onCreated={() => {}} />))
    const submit = screen.getByTestId('create-project-modal-submit') as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    await userEvent.type(screen.getByTestId('create-project-modal-name'), 'demo')
    expect(submit.disabled).toBe(false)
  })

  it('calls createProject and onCreated on success, then closes', async () => {
    const created: projectsApi.Project = {
      project_id: 'p9', name: 'demo', description: 'd',
      created_at: '2026-05-22T00:00:00Z', scan_count: 0,
    }
    const spy = vi.spyOn(projectsApi, 'createProject').mockResolvedValue(created)
    const onCreated = vi.fn()
    const onClose = vi.fn()
    render(wrap(<CreateProjectModal open onClose={onClose} onCreated={onCreated} />))
    await userEvent.type(screen.getByTestId('create-project-modal-name'), 'demo')
    await userEvent.type(screen.getByTestId('create-project-modal-description'), 'd')
    await userEvent.click(screen.getByTestId('create-project-modal-submit'))
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ name: 'demo', description: 'd' }))
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created))
    expect(onClose).toHaveBeenCalled()
  })

  it('shows the duplicate-name error inline and stays open', async () => {
    vi.spyOn(projectsApi, 'createProject').mockRejectedValue(
      Object.assign(new Error('taken'), { status: 409, code: 'duplicate_project_name' })
    )
    const onClose = vi.fn()
    render(wrap(<CreateProjectModal open onClose={onClose} onCreated={() => {}} />))
    await userEvent.type(screen.getByTestId('create-project-modal-name'), 'dup')
    await userEvent.click(screen.getByTestId('create-project-modal-submit'))
    await waitFor(() =>
      expect(screen.getByTestId('create-project-modal-error').textContent ?? '').toMatch(/taken|duplicate/i)
    )
    expect(onClose).not.toHaveBeenCalled()
  })
})
