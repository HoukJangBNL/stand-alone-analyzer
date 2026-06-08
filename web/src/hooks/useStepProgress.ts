// web/src/hooks/useStepProgress.ts
/**
 * useStepProgress hook per integrated design §6 (extended for Plan 2 to
 * surface the 'done' event's result payload). W3.1 — also fires toast.error
 * on SSE error events. Task 4 (gpu-dispatcher) — also surfaces gpu_launching
 * and gpu_ready SSE events so callers can render cold-start UX badges.
 */
import { useState, useCallback, useRef } from 'react'
import { toast } from 'sonner'
import { parseEventStream } from '@/lib/sse'
import { postSseRun } from '@/api/sseRun'

type StepStatus = 'idle' | 'running' | 'done' | 'error'
type GpuStatus = 'idle' | 'launching' | 'ready'

export function useStepProgress<P = unknown, R = unknown>(
  projectId: string,
  scanId: string | number,
  step: string
) {
  const [status, setStatus] = useState<StepStatus>('idle')
  const [pct, setPct] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<R | null>(null)
  const [gpuStatus, setGpuStatus] = useState<GpuStatus>('idle')
  const [gpuInstanceId, setGpuInstanceId] = useState<string | null>(null)
  const [gpuImageCount, setGpuImageCount] = useState<number | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)

  const start = useCallback(
    async (params: P) => {
      abortControllerRef.current = new AbortController()
      setStatus('running')
      setPct(0)
      setMessage('')
      setResult(null)
      // Reset GPU cold-start state — a fresh run may launch again or hit a
      // still-warm worker; the next gpu_launching / gpu_ready event will
      // re-populate.
      setGpuStatus('idle')
      setGpuInstanceId(null)
      setGpuImageCount(null)

      try {
        const response = await postSseRun(
          projectId,
          scanId,
          step,
          params,
          abortControllerRef.current.signal
        )
        for await (const event of parseEventStream(
          response,
          abortControllerRef.current.signal
        )) {
          if (event.type === 'progress') {
            setPct(event.data.pct)
            setMessage(event.data.msg || '')
          } else if (event.type === 'gpu_launching') {
            setGpuStatus('launching')
            setGpuInstanceId(
              typeof event.data?.instance_id === 'string'
                ? event.data.instance_id
                : null
            )
          } else if (event.type === 'gpu_ready') {
            setGpuStatus('ready')
            const ic = event.data?.image_count
            setGpuImageCount(typeof ic === 'number' ? ic : null)
          } else if (event.type === 'done') {
            setResult((event.data?.result ?? null) as R | null)
            setStatus('done')
            setPct(1)
            break
          } else if (event.type === 'error') {
            const msg = event.data.error?.message || 'Pipeline failed'
            setStatus('error')
            setMessage(msg)
            toast.error(msg)
            break
          }
        }
      } catch (err: unknown) {
        const e = err as { name?: string; message?: string }
        if (e.name === 'AbortError') {
          setStatus('idle')
        } else {
          const msg = e.message ?? 'Network error'
          setStatus('error')
          setMessage(msg)
          toast.error(msg)
        }
      }
    },
    [projectId, scanId, step]
  )

  const cancel = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  return {
    status,
    pct,
    message,
    result,
    gpuStatus,
    gpuInstanceId,
    gpuImageCount,
    start,
    cancel,
  }
}
