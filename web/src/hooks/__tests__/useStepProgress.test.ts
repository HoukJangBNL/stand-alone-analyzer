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
      useStepProgress('local', 1, 'thumbnails')
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
      useStepProgress('local', 1, 'thumbnails')
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
      useStepProgress('local', 1, 'thumbnails')
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

    const { result } = renderHook(() => useStepProgress('local', 1, 'selector'))
    await act(async () => {
      await result.current.start({})
    })

    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.result).toEqual({ selected_count: 7, total_count: 12 })
  })

  it('result is null until done event arrives', () => {
    const { result } = renderHook(() => useStepProgress('local', 1, 'selector'))
    expect(result.current.result).toBeNull()
  })
})

describe('useStepProgress.scanId routing', () => {
  // Task C1 (upload-robustness): hook must forward scanId so the request hits
  // the scan-scoped backend route /projects/{pid}/scans/{sid}/run/{step}.
  it('builds the scan-scoped URL from (projectId, scanId, step)', async () => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode('event: done\ndata: {"result":{}}\n\n')
        )
        controller.close()
      },
    })
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      })
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() =>
      useStepProgress('p1', 's42', 'thumbnails')
    )
    await act(async () => {
      await result.current.start({})
    })

    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/projects/p1/scans/s42/run/thumbnails')
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

    const { result } = renderHook(() => useStepProgress('local', 1, 'background'))

    await act(async () => { await result.current.start({}) })
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(errSpy).toHaveBeenCalledWith('background step crashed')
  })
})

describe('useStepProgress GPU cold-start', () => {
  // Task 4 (gpu-dispatcher): hook must surface gpu_launching / gpu_ready SSE
  // events as gpuStatus / gpuInstanceId / gpuImageCount so SamRunPanel can
  // render cold-start badges before per-image progress takes over.
  function streamFromSSE(body: string) {
    const encoder = new TextEncoder()
    return new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(body))
        controller.close()
      },
    })
  }

  function mockFetchWithSSE(body: string) {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(streamFromSSE(body), {
          status: 200,
          headers: { 'content-type': 'text/event-stream' },
        })
      )
    )
  }

  it('exposes gpuStatus="launching" + gpuInstanceId on gpu_launching event', async () => {
    const sseBody = [
      'event: gpu_launching\ndata: {"instance_id":"i-abc123"}\n\n',
      'event: done\ndata: {"result":{"images":0}}\n\n',
    ].join('')
    mockFetchWithSSE(sseBody)

    const { result } = renderHook(() =>
      useStepProgress<unknown, { images: number }>('p', 1, 'sam')
    )

    await act(async () => {
      await result.current.start({})
    })

    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.gpuStatus).toBe('launching')
    expect(result.current.gpuInstanceId).toBe('i-abc123')
  })

  it('flips gpuStatus to "ready" + captures imageCount on gpu_ready event', async () => {
    const sseBody = [
      'event: gpu_launching\ndata: {"instance_id":"i-abc123"}\n\n',
      'event: gpu_ready\ndata: {"image_count":100}\n\n',
      'event: done\ndata: {"result":{"images":100}}\n\n',
    ].join('')
    mockFetchWithSSE(sseBody)

    const { result } = renderHook(() =>
      useStepProgress<unknown, { images: number }>('p', 1, 'sam')
    )

    await act(async () => {
      await result.current.start({})
    })

    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.gpuStatus).toBe('ready')
    expect(result.current.gpuInstanceId).toBe('i-abc123')
    expect(result.current.gpuImageCount).toBe(100)
  })

  it('default gpu state is idle/null when no gpu_* events fire', async () => {
    const sseBody = [
      'event: progress\ndata: {"pct":0.5,"msg":"half"}\n\n',
      'event: done\ndata: {"result":{"images":1}}\n\n',
    ].join('')
    mockFetchWithSSE(sseBody)

    const { result } = renderHook(() =>
      useStepProgress<unknown, { images: number }>('p', 1, 'sam')
    )

    await act(async () => {
      await result.current.start({})
    })

    await waitFor(() => expect(result.current.status).toBe('done'))
    expect(result.current.gpuStatus).toBe('idle')
    expect(result.current.gpuInstanceId).toBeNull()
    expect(result.current.gpuImageCount).toBeNull()
  })
})
