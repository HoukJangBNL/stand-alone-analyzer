// web/src/hooks/usePipelineProgress.ts
/**
 * P5.3 — usePipelineProgress.
 *
 * Drives the unified 5-step pipeline UX from the
 * `POST /api/v1/projects/{pid}/scans/{sid}/run/pipeline` SSE stream.
 *
 * Public surface:
 *   - state.phase: 'idle' | 'running' | 'done' | 'error'
 *   - state.steps[stepName]: per-step status / pct / message / result
 *   - state.currentStep: most recent `step_started`
 *   - state.error: ApiError on transport failure or pipeline_error envelope
 *   - state.cascade: pipeline_done's cascade summary
 *   - start(body): kicks off the pipeline (POST + SSE consume)
 *   - cancel(): aborts the SSE stream
 *
 * Reuses {@link postSseRun} (with step="pipeline") to avoid duplicating the
 * auth/error envelope plumbing. The 5-event vocabulary is documented in
 * `src/flake_analysis/api/sse.py::PipelineProgressBridge`.
 */
import { useState, useCallback, useRef } from 'react'
import { parseEventStream } from '@/lib/sse'
import { postSseRun } from '@/api/sseRun'
import { ApiError } from '@/api/selector'

export type StepName =
  | 'thumbnails'
  | 'background'
  | 'sam'
  | 'domain_stats'
  | 'domain_proximity'

export const PIPELINE_STEPS: readonly StepName[] = [
  'thumbnails',
  'background',
  'sam',
  'domain_stats',
  'domain_proximity',
]

export interface StepState {
  status: 'idle' | 'running' | 'done' | 'error'
  pct: number
  message: string
  result: unknown
}

export interface PipelineErrorEnvelope {
  code: string
  message: string
  details?: unknown
  request_id?: string
}

export interface PipelineState {
  phase: 'idle' | 'running' | 'done' | 'error'
  steps: Record<StepName, StepState>
  currentStep: StepName | null
  error: ApiError | PipelineErrorEnvelope | null
  cascade: unknown | null
}

export interface PipelineBody {
  thumbnails?: {
    raw_ext?: string
    quality?: number
    force_recompute?: boolean
  }
  background?: {
    seed?: number
    max_images?: number
    gaussian_sigma?: number
    method?: string
  }
  sam: { weights_path: string; device?: string | null }
  domain_stats?: { repr_mode?: string; raw_ext?: string }
  domain_proximity?: {
    r_max_px?: number
    min_area_px?: number
    max_area_px?: number | null
    d_touch_px?: number
    pixel_size_um?: number
    link_distance_um?: number
    workers?: number
  }
}

function freshSteps(): Record<StepName, StepState> {
  const out = {} as Record<StepName, StepState>
  for (const step of PIPELINE_STEPS) {
    out[step] = { status: 'idle', pct: 0, message: '', result: null }
  }
  return out
}

const INITIAL_STATE: PipelineState = {
  phase: 'idle',
  steps: freshSteps(),
  currentStep: null,
  error: null,
  cascade: null,
}

type PipelineEvent =
  | {
      type: 'step_started'
      step: StepName
      index: number
      total: number
    }
  | {
      type: 'step_progress'
      step: StepName
      pct: number
      msg: string
    }
  | {
      type: 'step_completed'
      step: StepName
      result: unknown
    }
  | {
      type: 'pipeline_done'
      cascade?: unknown
      [k: string]: unknown
    }
  | {
      type: 'pipeline_error'
      step: StepName
      error: PipelineErrorEnvelope
    }

function reduce(state: PipelineState, ev: PipelineEvent): PipelineState {
  switch (ev.type) {
    case 'step_started': {
      const next = { ...state.steps }
      next[ev.step] = {
        ...next[ev.step],
        status: 'running',
        pct: 0,
        message: '',
      }
      return {
        ...state,
        phase: 'running',
        steps: next,
        currentStep: ev.step,
      }
    }
    case 'step_progress': {
      const cur = state.steps[ev.step]
      const next = { ...state.steps }
      next[ev.step] = {
        ...cur,
        status: 'running',
        pct: ev.pct,
        message: ev.msg ?? '',
      }
      return { ...state, steps: next }
    }
    case 'step_completed': {
      const next = { ...state.steps }
      next[ev.step] = {
        status: 'done',
        pct: 1,
        message: '',
        result: ev.result,
      }
      // Don't clear currentStep — leave it so the indicator carries over
      // until the next step_started or terminal event fires.
      return { ...state, steps: next }
    }
    case 'pipeline_done': {
      return {
        ...state,
        phase: 'done',
        currentStep: null,
        cascade: ev.cascade ?? null,
      }
    }
    case 'pipeline_error': {
      const next = { ...state.steps }
      const failing = next[ev.step] ?? {
        status: 'idle',
        pct: 0,
        message: '',
        result: null,
      }
      next[ev.step] = {
        ...failing,
        status: 'error',
        message: ev.error?.message ?? 'Pipeline failed',
      }
      return {
        ...state,
        phase: 'error',
        steps: next,
        error: ev.error,
      }
    }
    default:
      return state
  }
}

export function usePipelineProgress(
  projectId: string,
  scanId: string | number
) {
  const [state, setState] = useState<PipelineState>(() => ({
    ...INITIAL_STATE,
    steps: freshSteps(),
  }))
  const abortControllerRef = useRef<AbortController | null>(null)

  const start = useCallback(
    async (body: PipelineBody) => {
      abortControllerRef.current = new AbortController()
      setState({
        phase: 'running',
        steps: freshSteps(),
        currentStep: null,
        error: null,
        cascade: null,
      })

      try {
        const response = await postSseRun(
          projectId,
          scanId,
          'pipeline',
          body,
          abortControllerRef.current.signal
        )
        for await (const event of parseEventStream(
          response,
          abortControllerRef.current.signal
        )) {
          if (abortControllerRef.current?.signal.aborted) break
          // The parser yields {type, data} where data is the parsed JSON
          // payload. Each payload also carries its own `type` field — we
          // discriminate on that for narrowing.
          const payload = event.data as PipelineEvent
          if (!payload || typeof payload !== 'object') continue
          setState((prev) => reduce(prev, payload))
          if (
            payload.type === 'pipeline_done' ||
            payload.type === 'pipeline_error'
          ) {
            break
          }
        }
      } catch (err: unknown) {
        const e = err as { name?: string; message?: string }
        if (e.name === 'AbortError') {
          // User cancelled — keep whatever ran; just leave phase where it is.
          return
        }
        if (err instanceof ApiError) {
          setState((prev) => ({
            ...prev,
            phase: 'error',
            error: err,
          }))
        } else {
          setState((prev) => ({
            ...prev,
            phase: 'error',
            error: {
              code: 'transport_error',
              message: e.message ?? 'Network error',
            },
          }))
        }
      }
    },
    [projectId, scanId]
  )

  const cancel = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  return { state, start, cancel }
}
