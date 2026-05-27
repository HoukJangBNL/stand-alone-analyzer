// web/src/hooks/__tests__/usePipelineProgress.test.ts
/**
 * P5.3 — usePipelineProgress drives the unified pipeline UX from the
 * /run/pipeline SSE stream. Mocks fetch with a controllable ReadableStream
 * to walk through the 5-event vocabulary.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { usePipelineProgress } from '../usePipelineProgress'

const STEPS = [
  'thumbnails',
  'background',
  'sam',
  'domain_stats',
  'domain_proximity',
] as const

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

function makeStream(): {
  stream: ReadableStream<Uint8Array>
  push: (chunk: string) => Promise<void>
  close: () => void
} {
  const encoder = new TextEncoder()
  let resolveReady: (() => void) | null = null
  let ready = new Promise<void>((r) => {
    resolveReady = r
  })
  const queue: string[] = []
  let closed = false
  let controllerRef: ReadableStreamDefaultController<Uint8Array> | null = null

  let controllerClosed = false
  const tryClose = (controller: ReadableStreamDefaultController<Uint8Array>) => {
    if (controllerClosed) return
    controllerClosed = true
    try {
      controller.close()
    } catch {
      // already torn down
    }
  }
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controllerRef = controller
      // Pump pre-queued frames
      void (async () => {
        while (true) {
          await ready
          while (queue.length > 0) {
            const next = queue.shift()!
            try {
              controller.enqueue(encoder.encode(next))
            } catch {
              return
            }
          }
          if (closed) {
            tryClose(controller)
            return
          }
          ready = new Promise<void>((r) => {
            resolveReady = r
          })
        }
      })()
    },
    cancel() {
      controllerClosed = true
      closed = true
      resolveReady?.()
    },
  })

  const push = async (chunk: string) => {
    queue.push(chunk)
    resolveReady?.()
    // Yield to the microtask queue so the parser can consume.
    await Promise.resolve()
    await Promise.resolve()
  }
  const close = () => {
    closed = true
    resolveReady?.()
    if (controllerRef && queue.length === 0) {
      tryClose(controllerRef)
    }
  }
  return { stream, push, close }
}

function mockFetch(stream: ReadableStream<Uint8Array>) {
  return vi.fn().mockResolvedValue(
    new Response(stream, {
      status: 200,
      headers: { 'content-type': 'text/event-stream' },
    })
  )
}

describe('usePipelineProgress', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('initial state is fully idle', () => {
    const { result } = renderHook(() => usePipelineProgress('p1', 1))
    expect(result.current.state.phase).toBe('idle')
    expect(result.current.state.currentStep).toBeNull()
    expect(result.current.state.error).toBeNull()
    expect(result.current.state.cascade).toBeNull()
    for (const step of STEPS) {
      expect(result.current.state.steps[step].status).toBe('idle')
      expect(result.current.state.steps[step].pct).toBe(0)
      expect(result.current.state.steps[step].message).toBe('')
      expect(result.current.state.steps[step].result).toBeNull()
    }
  })

  it('step_started transitions phase to running and sets currentStep', async () => {
    const { stream, push, close } = makeStream()
    global.fetch = mockFetch(stream)

    const { result } = renderHook(() => usePipelineProgress('p1', 1))

    void act(() => {
      void result.current.start({
        sam: { weights_path: '/srv/sam/merged.pt' },
      })
    })

    await push(
      frame('step_started', {
        type: 'step_started',
        step: 'thumbnails',
        index: 0,
        total: 5,
      })
    )
    await waitFor(() =>
      expect(result.current.state.steps.thumbnails.status).toBe('running')
    )
    expect(result.current.state.phase).toBe('running')
    expect(result.current.state.currentStep).toBe('thumbnails')

    close()
  })

  it('step_progress updates pct and message on the current step', async () => {
    const { stream, push, close } = makeStream()
    global.fetch = mockFetch(stream)

    const { result } = renderHook(() => usePipelineProgress('p1', 1))

    void act(() => {
      void result.current.start({
        sam: { weights_path: '/srv/sam/merged.pt' },
      })
    })

    await push(
      frame('step_started', {
        type: 'step_started',
        step: 'thumbnails',
        index: 0,
        total: 5,
      })
    )
    await push(
      frame('step_progress', {
        type: 'step_progress',
        step: 'thumbnails',
        pct: 0.5,
        msg: 'halfway',
      })
    )

    await waitFor(() =>
      expect(result.current.state.steps.thumbnails.pct).toBe(0.5)
    )
    expect(result.current.state.steps.thumbnails.message).toBe('halfway')
    expect(result.current.state.steps.thumbnails.status).toBe('running')

    close()
  })

  it('step_completed marks step done with result, leaves currentStep set until next event', async () => {
    const { stream, push, close } = makeStream()
    global.fetch = mockFetch(stream)

    const { result } = renderHook(() => usePipelineProgress('p1', 1))

    void act(() => {
      void result.current.start({
        sam: { weights_path: '/srv/sam/merged.pt' },
      })
    })

    await push(
      frame('step_started', {
        type: 'step_started',
        step: 'thumbnails',
        index: 0,
        total: 5,
      })
    )
    await push(
      frame('step_completed', {
        type: 'step_completed',
        step: 'thumbnails',
        result: { n_images: 7 },
      })
    )

    await waitFor(() =>
      expect(result.current.state.steps.thumbnails.status).toBe('done')
    )
    expect(result.current.state.steps.thumbnails.pct).toBe(1)
    expect(result.current.state.steps.thumbnails.message).toBe('')
    expect(result.current.state.steps.thumbnails.result).toEqual({ n_images: 7 })
    // currentStep is left in place — orchestrator emits next step_started.
    expect(result.current.state.currentStep).toBe('thumbnails')

    close()
  })

  it('multi-step transitions: thumbnails done → background running', async () => {
    const { stream, push, close } = makeStream()
    global.fetch = mockFetch(stream)

    const { result } = renderHook(() => usePipelineProgress('p1', 1))

    void act(() => {
      void result.current.start({
        sam: { weights_path: '/srv/sam/merged.pt' },
      })
    })

    await push(
      frame('step_started', {
        type: 'step_started',
        step: 'thumbnails',
        index: 0,
        total: 5,
      })
    )
    await push(
      frame('step_completed', {
        type: 'step_completed',
        step: 'thumbnails',
        result: {},
      })
    )
    await push(
      frame('step_started', {
        type: 'step_started',
        step: 'background',
        index: 1,
        total: 5,
      })
    )

    await waitFor(() =>
      expect(result.current.state.steps.background.status).toBe('running')
    )
    expect(result.current.state.steps.thumbnails.status).toBe('done')
    expect(result.current.state.currentStep).toBe('background')

    close()
  })

  it('pipeline_done sets phase=done and exposes cascade summary', async () => {
    const { stream, push, close } = makeStream()
    global.fetch = mockFetch(stream)

    const { result } = renderHook(() => usePipelineProgress('p1', 1))

    void act(() => {
      void result.current.start({
        sam: { weights_path: '/srv/sam/merged.pt' },
      })
    })

    // Walk through every step quickly
    for (let i = 0; i < STEPS.length; i++) {
      const step = STEPS[i]
      await push(
        frame('step_started', { type: 'step_started', step, index: i, total: 5 })
      )
      await push(
        frame('step_completed', { type: 'step_completed', step, result: {} })
      )
    }
    await push(
      frame('pipeline_done', {
        type: 'pipeline_done',
        cascade: { fired: false },
      })
    )

    await waitFor(() => expect(result.current.state.phase).toBe('done'))
    expect(result.current.state.currentStep).toBeNull()
    expect(result.current.state.cascade).toEqual({ fired: false })

    close()
  })

  it('pipeline_error mid-run marks failing step as error and surfaces ApiError', async () => {
    const { stream, push, close } = makeStream()
    global.fetch = mockFetch(stream)

    const { result } = renderHook(() => usePipelineProgress('p1', 1))

    void act(() => {
      void result.current.start({
        sam: { weights_path: '/srv/sam/merged.pt' },
      })
    })

    await push(
      frame('step_started', {
        type: 'step_started',
        step: 'thumbnails',
        index: 0,
        total: 5,
      })
    )
    await push(
      frame('step_completed', {
        type: 'step_completed',
        step: 'thumbnails',
        result: {},
      })
    )
    await push(
      frame('step_started', {
        type: 'step_started',
        step: 'sam',
        index: 2,
        total: 5,
      })
    )
    await push(
      frame('pipeline_error', {
        type: 'pipeline_error',
        step: 'sam',
        error: {
          code: 'sam_failed',
          message: 'weights missing',
          details: {},
          request_id: 'req-1',
        },
      })
    )

    await waitFor(() => expect(result.current.state.phase).toBe('error'))
    expect(result.current.state.steps.sam.status).toBe('error')
    expect(result.current.state.steps.sam.message).toBe('weights missing')
    expect(result.current.state.error).not.toBeNull()
    expect(result.current.state.error?.code).toBe('sam_failed')
    expect(result.current.state.error?.message).toBe('weights missing')

    close()
  })

  it('cancel() aborts the stream without further state churn', async () => {
    const { stream, push, close } = makeStream()
    const fetchMock = mockFetch(stream)
    global.fetch = fetchMock

    const { result } = renderHook(() => usePipelineProgress('p1', 1))

    void act(() => {
      void result.current.start({
        sam: { weights_path: '/srv/sam/merged.pt' },
      })
    })

    await push(
      frame('step_started', {
        type: 'step_started',
        step: 'thumbnails',
        index: 0,
        total: 5,
      })
    )
    await waitFor(() =>
      expect(result.current.state.steps.thumbnails.status).toBe('running')
    )

    act(() => {
      result.current.cancel()
    })

    // Push another frame after abort — must not flip steps.background.status.
    await push(
      frame('step_started', {
        type: 'step_started',
        step: 'background',
        index: 1,
        total: 5,
      })
    )

    // Allow microtasks to settle
    await Promise.resolve()
    await Promise.resolve()

    expect(result.current.state.steps.background.status).toBe('idle')

    close()
  })

  it('builds the scan-scoped pipeline URL', async () => {
    const { stream, push, close } = makeStream()
    const fetchMock = mockFetch(stream)
    global.fetch = fetchMock

    const { result } = renderHook(() => usePipelineProgress('p42', 's7'))

    void act(() => {
      void result.current.start({
        sam: { weights_path: '/srv/sam/merged.pt' },
      })
    })

    await push(
      frame('pipeline_done', { type: 'pipeline_done', cascade: null })
    )
    await waitFor(() => expect(result.current.state.phase).toBe('done'))

    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/projects/p42/scans/s7/run/pipeline')

    close()
  })
})
