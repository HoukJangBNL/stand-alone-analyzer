// web/src/hooks/useStepProgress.ts
/**
 * useStepProgress hook per integrated design §6.
 * Single-connection POST→SSE per §3.2.
 */
import { useState, useCallback, useRef } from 'react'
import { parseEventStream } from '@/lib/sse'

type StepStatus = 'idle' | 'running' | 'done' | 'error'

export function useStepProgress<P = any>(
  projectId: string,
  step: string
) {
  const [status, setStatus] = useState<StepStatus>('idle')
  const [pct, setPct] = useState(0)
  const [message, setMessage] = useState('')
  const abortControllerRef = useRef<AbortController | null>(null)

  const start = useCallback(
    async (params: P) => {
      abortControllerRef.current = new AbortController()

      setStatus('running')
      setPct(0)
      setMessage('')

      try {
        const response = await fetch(
          `/api/v1/projects/${projectId}/run/${step}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
            signal: abortControllerRef.current.signal,
          }
        )

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }

        for await (const event of parseEventStream(
          response,
          abortControllerRef.current.signal
        )) {
          if (event.type === 'progress') {
            setPct(event.data.pct)
            setMessage(event.data.msg || '')
          } else if (event.type === 'done') {
            setStatus('done')
            setPct(1)
            break
          } else if (event.type === 'error') {
            setStatus('error')
            setMessage(event.data.error?.message || 'Pipeline failed')
            break
          }
        }
      } catch (err: any) {
        if (err.name === 'AbortError') {
          setStatus('idle')
        } else {
          setStatus('error')
          setMessage(err.message)
        }
      }
    },
    [projectId, step]
  )

  const cancel = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  return { status, pct, message, start, cancel }
}
