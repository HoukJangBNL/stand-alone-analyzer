import { describe, it, expect } from 'vitest'
import { parseEventStream } from '../sse'

describe('parseEventStream', () => {
  it('parses SSE events from ReadableStream', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: progress\n'))
        controller.enqueue(encoder.encode('data: {"pct":0.5,"msg":"halfway"}\n\n'))
        controller.enqueue(encoder.encode('event: done\n'))
        controller.enqueue(encoder.encode('data: {"result":{"n":10}}\n\n'))
        controller.close()
      },
    })

    const response = new Response(stream)
    const events = []

    for await (const event of parseEventStream(response)) {
      events.push(event)
    }

    expect(events).toHaveLength(2)
    expect(events[0].type).toBe('progress')
    expect(events[0].data.pct).toBe(0.5)
    expect(events[1].type).toBe('done')
  })

  it('supports AbortSignal cancellation', async () => {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('event: progress\n'))
        controller.enqueue(encoder.encode('data: {"pct":0.0}\n\n'))
      },
    })

    const response = new Response(stream)
    const abortController = new AbortController()

    const events = []
    const iterator = parseEventStream(response, abortController.signal)

    for await (const event of iterator) {
      events.push(event)
      abortController.abort()
    }

    expect(events).toHaveLength(1)
  })
})
