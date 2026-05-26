// web/src/hooks/useClusteringApplyThresholds.ts
import { useState, useCallback, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { parseEventStream } from '@/lib/sse'
import { postSseRun } from '@/api/sseRun'
import type { ApplyThresholdsBody } from '@/api/clustering'

interface ApplySummary {
  n_pass: number
  n_total: number
  n_clusters: number
}

type ApplyStatus = 'idle' | 'running' | 'done' | 'error'

export function useClusteringApplyThresholds(
  projectId: string,
  scanId: string | number
) {
  const qc = useQueryClient()
  const [status, setStatus] = useState<ApplyStatus>('idle')
  const [pct, setPct] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<ApplySummary | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const run = useCallback(
    async (body: ApplyThresholdsBody) => {
      abortRef.current = new AbortController()
      setStatus('running')
      setPct(0)
      setMessage('')
      setResult(null)
      try {
        const response = await postSseRun(
          projectId,
          scanId,
          'clustering/apply_thresholds',
          body,
          abortRef.current.signal
        )
        for await (const event of parseEventStream(response, abortRef.current.signal)) {
          if (event.type === 'progress') {
            setPct(event.data.pct)
            setMessage(event.data.msg || '')
          } else if (event.type === 'done') {
            setResult((event.data?.result ?? null) as ApplySummary | null)
            setStatus('done')
            setPct(1)
            qc.invalidateQueries({ queryKey: ['clustering', 'labels', projectId] })
            qc.invalidateQueries({ queryKey: ['clustering', 'assignments', projectId] })
            break
          } else if (event.type === 'error') {
            const msg = event.data.error?.message || 'Apply thresholds failed'
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
    [projectId, scanId, qc]
  )

  const cancel = useCallback(() => abortRef.current?.abort(), [])

  return { status, pct, message, result, run, cancel }
}
