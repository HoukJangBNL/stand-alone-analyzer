// web/src/hooks/__tests__/useStepProgress.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { toast } from 'sonner'
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

describe('useStepProgress.result', () => {
  it('exposes the done event payload', async () => {
    const sseBody = [
      'event: progress\ndata: {"type":"progress","pct":0.5,"msg":"halfway"}\n\n',
      'event: done\ndata: {"type":"done","result":{"selected_count":7,"total_count":12}}\n\n',
    ].join('')

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(sseBody))
        controller.close()
      },
    })

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { 'content-type': 'text/event-stream' },
        })
      )
    )

    const { result } = renderHook(() => useStepProgress('local', 'selector'))
    await act(async () => {
      await result.current.start({})
    })

    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.result).toEqual({ selected_count: 7, total_count: 12 })
  })

  it('result is null until done event arrives', () => {
    const { result } = renderHook(() => useStepProgress('local', 'selector'))
    expect(result.current.result).toBeNull()
  })
})

describe('useStepProgress.toast on SSE error', () => {
  it('calls toast.error with the SSE error envelope message', async () => {
    const errSpy = vi.spyOn(toast, 'error').mockImplementation(() => 'mock-id')

    const encoder = new TextEncoder()
    const errorPayload = {
      error: { code: 'pipeline_failed', message: 'background step crashed' },
    }
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(`event: error\ndata: ${JSON.stringify(errorPayload)}\n\n`)
        )
        controller.close()
      },
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(stream, { status: 200, headers: { 'content-type': 'text/event-stream' } })
      )
    )

    const { result } = renderHook(() => useStepProgress('local', 'background'))

    await act(async () => { await result.current.start({}) })
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(errSpy).toHaveBeenCalledWith('background step crashed')
  })
})
