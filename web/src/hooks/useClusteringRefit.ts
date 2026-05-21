// web/src/hooks/useClusteringRefit.ts
import { useState, useCallback, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { parseEventStream } from '@/lib/sse'
import { postSseRun } from '@/api/sseRun'
import type { ClusteringRefitBody } from '@/api/clustering'

interface RefitResult {
  n_clusters: number
  n_assigned: number
  n_unassigned: number
  output_dir: string
  reg_covar_chosen?: number
}

type RefitStatus = 'idle' | 'running' | 'done' | 'error'

export function useClusteringRefit(projectId: string) {
  const qc = useQueryClient()
  const [status, setStatus] = useState<RefitStatus>('idle')
  const [pct, setPct] = useState(0)
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<RefitResult | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const run = useCallback(
    async (body: ClusteringRefitBody) => {
      abortRef.current = new AbortController()
      setStatus('running')
      setPct(0)
      setMessage('')
      setResult(null)
      try {
        const response = await postSseRun(
          projectId,
          'clustering/refit',
          body,
          abortRef.current.signal
        )
        for await (const event of parseEventStream(response, abortRef.current.signal)) {
          if (event.type === 'progress') {
            setPct(event.data.pct)
            setMessage(event.data.msg || '')
          } else if (event.type === 'done') {
            setResult((event.data?.result ?? null) as RefitResult | null)
            setStatus('done')
            setPct(1)
            qc.invalidateQueries({ queryKey: ['clustering', 'labels', projectId] })
            qc.invalidateQueries({ queryKey: ['clustering', 'assignments', projectId] })
            qc.invalidateQueries({ queryKey: ['clustering', 'seed_groups', projectId] })
            break
          } else if (event.type === 'error') {
            const msg = event.data.error?.message || 'Refit failed'
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
    [projectId, qc]
  )

  const cancel = useCallback(() => abortRef.current?.abort(), [])

  return { status, pct, message, result, run, cancel }
}
