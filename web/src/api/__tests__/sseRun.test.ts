// web/src/api/__tests__/sseRun.test.ts
// Task C1 (upload-robustness): postSseRun must build a scan-scoped URL so
// Compute Run hits /api/v1/projects/{pid}/scans/{sid}/run/{step} (the actual
// backend route). Without scanId in the path the request 404s.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { postSseRun } from '../sseRun'

describe('postSseRun URL', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('builds a scan-scoped URL when scanId is provided', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('', {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      })
    )
    vi.stubGlobal('fetch', fetchMock)

    const ctrl = new AbortController()
    await postSseRun('p1', 's42', 'thumbnails', {}, ctrl.signal)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toBe('/api/v1/projects/p1/scans/s42/run/thumbnails')
  })

  it('numeric scanId serializes into the path', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response('', {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      })
    )
    vi.stubGlobal('fetch', fetchMock)

    const ctrl = new AbortController()
    await postSseRun('proj-A', 7, 'background', {}, ctrl.signal)

    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/projects/proj-A/scans/7/run/background')
  })
})
