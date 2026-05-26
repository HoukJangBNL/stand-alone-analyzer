import { describe, it, expect, beforeEach, vi } from 'vitest'
import { deleteScan } from '@/api/upload'

const fetchMock = vi.fn()
beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

describe('deleteScan', () => {
  it('issues DELETE /api/v1/scans/{scan_id} with auth headers and resolves on 204', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))

    await deleteScan(42)

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/scans/42',
      expect.objectContaining({
        method: 'DELETE',
        credentials: 'include',
      })
    )
  })

  it('throws an error built from the JSON envelope on non-204 response', async () => {
    // Use mockImplementation so each call gets a fresh Response (Response body is single-consumption).
    fetchMock.mockImplementation(() => Promise.resolve(new Response(
      JSON.stringify({ error: { code: 'forbidden', details: { action: 'scan_edit' }, request_id: 'r-1' } }),
      { status: 403, headers: { 'content-type': 'application/json' } }
    )))

    await expect(deleteScan(42)).rejects.toThrow(/forbidden/)
    await expect(deleteScan(42)).rejects.toThrow(/scan_edit/)
  })
})
