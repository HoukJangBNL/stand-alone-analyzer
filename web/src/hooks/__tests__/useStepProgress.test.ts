// web/src/hooks/__tests__/useStepProgress.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useStepProgress } from '../useStepProgress'

describe('useStepProgress', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('starts with idle status', () => {
    const { result } = renderHook(() =>
      useStepProgress('local', 'thumbnails')
    )

    expect(result.current.status).toBe('idle')
    expect(result.current.pct).toBe(0)
  })

  it('streams progress events and completes', async () => {
    const encoder = new TextEncoder()

    let releaseDone!: () => void
    const doneReleased = new Promise<void>((r) => {
      releaseDone = r
    })

    const mockStream = new ReadableStream({
      async start(controller) {
        controller.enqueue(
          encoder.encode('event: progress\ndata: {"pct":0.5,"msg":"halfway"}\n\n')
        )
        await doneReleased
        controller.enqueue(
          encoder.encode('event: done\ndata: {"result":{"n":10}}\n\n')
        )
        controller.close()
      },
    })

    global.fetch = vi.fn().mockResolvedValue(
      new Response(mockStream, {
        headers: { 'content-type': 'text/event-stream' },
      })
    )

    const { result } = renderHook(() =>
      useStepProgress('local', 'thumbnails')
    )

    act(() => {
      result.current.start({ quality: 80 })
    })

    await waitFor(() => expect(result.current.status).toBe('running'))
    await waitFor(() => expect(result.current.pct).toBe(0.5))
    releaseDone()
    await waitFor(() => expect(result.current.status).toBe('done'))
  })

  it('surfaces SSE error envelope message and sets error status', async () => {
    const encoder = new TextEncoder()
    const errorPayload = {
      error: {
        code: 'pipeline_failed',
        message: 'thumbnails step crashed',
        details: { exc_type: 'RuntimeError' },
        request_id: 'req-123',
      },
    }

    const mockStream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            `event: error\ndata: ${JSON.stringify(errorPayload)}\n\n`
          )
        )
        controller.close()
      },
    })

    global.fetch = vi.fn().mockResolvedValue(
      new Response(mockStream, {
        headers: { 'content-type': 'text/event-stream' },
      })
    )

    const { result } = renderHook(() =>
      useStepProgress('local', 'thumbnails')
    )

    act(() => {
      result.current.start({})
    })

    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.message).toBe('thumbnails step crashed')
  })
})
