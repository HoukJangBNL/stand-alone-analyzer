// web/src/hooks/useClusteringRefit.ts
import { useState, useCallback, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { parseEventStream } from '@/lib/sse'
import type { ClusteringRefitBody } from '@/api/clustering'

interface RefitResult {
  n_clusters: number
  n_assigned: number
  n_unassigned: number
  output_dir: string
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
        const response = await fetch(
          `/api/v1/projects/${projectId}/run/clustering/refit`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: abortRef.current.signal,
          }
        )
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
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
            setStatus('error')
            setMessage(event.data.error?.message || 'Refit failed')
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
    [projectId, qc]
  )

  const cancel = useCallback(() => abortRef.current?.abort(), [])

  return { status, pct, message, result, run, cancel }
}
