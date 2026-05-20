/**
 * SSE parser per integrated design §3.2.
 * Parses text/event-stream from fetch() Response (POST-based SSE).
 */

export interface SSEEvent {
  type: string
  data: any
}

export async function* parseEventStream(
  response: Response,
  signal?: AbortSignal
): AsyncGenerator<SSEEvent, void, unknown> {
  if (!response.body) {
    throw new Error('Response body is null')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let currentEvent: { type?: string; data?: string } = {}

  try {
    while (true) {
      if (signal?.aborted) {
        break
      }

      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent.type = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          currentEvent.data = line.slice(6).trim()
        } else if (line === '' && currentEvent.type && currentEvent.data) {
          yield {
            type: currentEvent.type,
            data: JSON.parse(currentEvent.data),
          }
          currentEvent = {}
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
