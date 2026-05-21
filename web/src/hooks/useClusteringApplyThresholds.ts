// web/src/hooks/useClusteringApplyThresholds.ts
import { useState, useCallback, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { parseEventStream } from '@/lib/sse'
import type { ApplyThresholdsBody } from '@/api/clustering'

interface ApplySummary {
  n_pass: number
  n_total: number
  n_clusters: number
}

type ApplyStatus = 'idle' | 'running' | 'done' | 'error'

export function useClusteringApplyThresholds(projectId: string) {
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
        const response = await fetch(
          `/api/v1/projects/${projectId}/run/clustering/apply_thresholds`,
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
            setResult((event.data?.result ?? null) as ApplySummary | null)
            setStatus('done')
            setPct(1)
            qc.invalidateQueries({ queryKey: ['clustering', 'labels', projectId] })
            qc.invalidateQueries({ queryKey: ['clustering', 'assignments', projectId] })
            break
          } else if (event.type === 'error') {
            setStatus('error')
            setMessage(event.data.error?.message || 'Apply thresholds failed')
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
