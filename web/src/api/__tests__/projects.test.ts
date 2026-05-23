import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  listProjects,
  createProject,
  getProject,
  patchProject,
  deleteProject,
  type Project,
} from '@/api/projects'
import { ApiError } from '@/api/selector'

const fetchMock = vi.fn()
beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

function jsonResp(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('listProjects', () => {
  it('returns the projects array on 200', async () => {
    const sample: Project[] = [
      { project_id: 'p1', name: 'demo', description: null, created_at: '2026-05-22T00:00:00Z', scan_count: 0 },
    ]
    fetchMock.mockResolvedValue(jsonResp(200, { projects: sample }))
    const out = await listProjects()
    expect(out).toEqual(sample)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects',
      expect.objectContaining({ credentials: 'include' })
    )
  })

  it('throws ApiError on 500 with envelope', async () => {
    fetchMock.mockResolvedValue(jsonResp(500, { error: { code: 'db_unavailable', message: 'down' } }))
    await expect(listProjects()).rejects.toBeInstanceOf(ApiError)
  })
})

describe('createProject', () => {
  it('POSTs name + description and returns the new project', async () => {
    const created: Project = { project_id: 'p9', name: 'new', description: 'd', created_at: '2026-05-22T00:00:00Z', scan_count: 0 }
    fetchMock.mockResolvedValue(jsonResp(201, created))
    const out = await createProject({ name: 'new', description: 'd' })
    expect(out).toEqual(created)
    const [, opts] = fetchMock.mock.calls[0]
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual({ name: 'new', description: 'd' })
  })

  it('omits description when undefined', async () => {
    fetchMock.mockResolvedValue(jsonResp(201, { project_id: 'p9', name: 'x', description: null, created_at: 't', scan_count: 0 }))
    await createProject({ name: 'x' })
    const [, opts] = fetchMock.mock.calls[0]
    expect(JSON.parse(opts.body)).toEqual({ name: 'x' })
  })

  it('surfaces 409 duplicate as ApiError', async () => {
    fetchMock.mockResolvedValue(jsonResp(409, { error: { code: 'duplicate_project_name', message: 'taken' } }))
    await expect(createProject({ name: 'dup' })).rejects.toMatchObject({
      status: 409,
      code: 'duplicate_project_name',
    })
  })
})

describe('getProject / patchProject / deleteProject', () => {
  it('getProject hits the per-id endpoint', async () => {
    fetchMock.mockResolvedValue(jsonResp(200, { project_id: 'p1', name: 'a', description: null, created_at: 't', scan_count: 3 }))
    const p = await getProject('p1')
    expect(p.scan_count).toBe(3)
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/projects/p1', expect.objectContaining({ credentials: 'include' }))
  })

  it('patchProject sends only provided fields', async () => {
    fetchMock.mockResolvedValue(jsonResp(200, { project_id: 'p1', name: 'a2', description: null, created_at: 't', scan_count: 0 }))
    await patchProject('p1', { name: 'a2' })
    const [, opts] = fetchMock.mock.calls[0]
    expect(opts.method).toBe('PATCH')
    expect(JSON.parse(opts.body)).toEqual({ name: 'a2' })
  })

  it('deleteProject returns void on 204', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await deleteProject('p1')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects/p1',
      expect.objectContaining({ method: 'DELETE' })
    )
  })

  it('deleteProject surfaces 409 has_scans', async () => {
    fetchMock.mockResolvedValue(jsonResp(409, { error: { code: 'project_has_scans', message: 'cannot delete', details: { scan_count: 2 } } }))
    await expect(deleteProject('p1')).rejects.toMatchObject({
      status: 409,
      code: 'project_has_scans',
    })
  })
})
