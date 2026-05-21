// web/src/api/__tests__/projects.test.ts
import { describe, it, expect, vi, afterEach } from 'vitest'
import {
  fetchProjects,
  createProject,
  fetchActiveProject,
  type ProjectHandle,
} from '@/api/projects'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('api/projects', () => {
  it('fetchProjects returns the list when GET /projects succeeds', async () => {
    const handles: ProjectHandle[] = [
      { project_id: 'p1', analysis_folder: '/a' },
      { project_id: 'p2', analysis_folder: '/b' },
    ]
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ projects: handles }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    )
    expect(await fetchProjects()).toEqual(handles)
  })

  it('fetchProjects falls back to GET /projects/active on 404', async () => {
    const active: ProjectHandle = { project_id: 'local', analysis_folder: '/x' }
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(new Response('not found', { status: 404 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify(active), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    vi.stubGlobal('fetch', fetchSpy)
    expect(await fetchProjects()).toEqual([active])
    expect(fetchSpy).toHaveBeenCalledTimes(2)
  })

  it('createProject POSTs the three paths and returns the handle', async () => {
    const handle: ProjectHandle = {
      project_id: 'p3',
      analysis_folder: '/an',
      raw_images_dir: '/raw',
      annotations_path: '/an/annotations.json',
    }
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(handle), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )
    vi.stubGlobal('fetch', fetchSpy)
    const out = await createProject({
      analysis_folder: '/an',
      raw_images_dir: '/raw',
      annotations_path: '/an/annotations.json',
    })
    expect(out).toEqual(handle)
    const [url, init] = fetchSpy.mock.calls[0]
    expect(url).toBe('/api/v1/projects')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({
      analysis_folder: '/an',
      raw_images_dir: '/raw',
      annotations_path: '/an/annotations.json',
    })
  })

  it('fetchActiveProject hits /projects/active', async () => {
    const handle: ProjectHandle = { project_id: 'local', analysis_folder: '/x' }
    const fetchSpy = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(handle), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )
    vi.stubGlobal('fetch', fetchSpy)
    expect(await fetchActiveProject()).toEqual(handle)
    expect(fetchSpy.mock.calls[0][0]).toBe('/api/v1/projects/active')
  })
})
