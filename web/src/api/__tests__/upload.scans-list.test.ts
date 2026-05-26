import { describe, it, expect, beforeEach, vi } from 'vitest'
import { listScansForProject, type ScanSummary } from '@/api/upload'

const fetchMock = vi.fn()
beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

describe('listScansForProject', () => {
  it('returns scans on 200', async () => {
    const sample: ScanSummary[] = [
      { scan_id: 1, name: 's1', material: 'graphene', image_count: 4, uploaded_count: 4, status: 'ready', created_at: '2026-05-22T00:00:00Z' },
    ]
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ scans: sample }), {
      status: 200, headers: { 'content-type': 'application/json' },
    }))
    const out = await listScansForProject('p1')
    expect(out).toEqual(sample)
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/projects/p1/scans',
      expect.objectContaining({ credentials: 'include' })
    )
  })
})
