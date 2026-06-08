// web/src/components/run/SamRunPanel.tsx
/**
 * P3.2 — SAM run panel for ComputeTab.
 *
 * Wraps useStepProgress for the "sam" step. Per-image progress messages from
 * the backend look like "[i/N] basename: K masks" or carry an "ERROR ..."
 * token; the latter renders in red so users notice partial failures while the
 * stream keeps going.
 *
 * Weights path is read from VITE_SAM_WEIGHTS_PATH (build-time env) and falls
 * back to the canonical bastion path. Device is intentionally omitted — the
 * server picks it up from its own runtime config.
 */
import { useStepProgress } from '@/hooks/useStepProgress'

interface SamRunPanelProps {
  projectId: string
  scanId: number
}

interface SamParams {
  weights_path: string
}

interface SamResult {
  images: number
  masks_total: number
  errors: number
}

const DEFAULT_WEIGHTS = '/srv/sam/merged.pt'

function isErrorMessage(msg: string): boolean {
  return /error/i.test(msg)
}

export function SamRunPanel({ projectId, scanId }: SamRunPanelProps) {
  const {
    status,
    pct,
    message,
    result,
    gpuStatus,
    gpuInstanceId,
    gpuImageCount,
    start,
    cancel,
  } = useStepProgress<SamParams, SamResult>(projectId, scanId, 'sam')

  // Vite injects env vars on import.meta.env. The project doesn't ship a
  // vite-env.d.ts, so funnel through `unknown` to satisfy strict TS.
  const env = (import.meta as unknown as { env?: Record<string, string> }).env
  const weightsPath = env?.VITE_SAM_WEIGHTS_PATH ?? DEFAULT_WEIGHTS

  const handleRun = () => {
    void start({ weights_path: weightsPath })
  }

  const isRunning = status === 'running'
  const isDone = status === 'done'
  const isError = status === 'error'
  const messageIsError =
    (isRunning || isError) && message.length > 0 && isErrorMessage(message)

  return (
    <div
      data-testid="compute-sam-card"
      style={{ border: '1px solid #ccc', padding: '16px', margin: '8px 0' }}
    >
      <h3>SAM</h3>

      <button
        data-testid="compute-sam-run"
        onClick={handleRun}
        disabled={isRunning}
      >
        Run SAM
      </button>

      {isRunning && (
        <button
          data-testid="compute-sam-cancel"
          onClick={cancel}
          style={{ marginLeft: '8px' }}
        >
          Cancel
        </button>
      )}

      {/* Cold-start UX (Task 4): show GPU launch + ready badges before
          per-image progress takes over. The launching badge surfaces the
          EC2 instance id so users know a cold spot-up is in flight. */}
      {gpuStatus === 'launching' && gpuInstanceId && (
        <div
          data-testid="sam-progress-gpu-launching"
          style={{ marginTop: '8px', fontSize: '0.9em', color: '#1d4ed8' }}
        >
          Launching GPU instance ({gpuInstanceId})…
        </div>
      )}
      {gpuStatus === 'ready' && gpuImageCount !== null && (
        <div
          data-testid="sam-progress-gpu-ready"
          style={{ marginTop: '8px', fontSize: '0.9em', color: '#15803d' }}
        >
          ✓ GPU ready, processing {gpuImageCount} images
        </div>
      )}

      {isRunning && (
        <div style={{ marginTop: '8px' }}>
          <progress
            data-testid="compute-sam-pct"
            value={pct}
            max={1}
            style={{ width: '100%' }}
          />
          <div
            data-testid="compute-sam-msg"
            style={{ color: messageIsError ? 'red' : undefined }}
          >
            {message}
          </div>
        </div>
      )}

      {isDone && (
        <div style={{ marginTop: '8px' }}>
          <div style={{ color: 'green' }}>✓ Complete</div>
          {result && (
            <div data-testid="compute-sam-summary" style={{ fontSize: '0.85em', color: '#555' }}>
              images: {result.images} · masks_total: {result.masks_total} · errors: {result.errors}
            </div>
          )}
        </div>
      )}

      {isError && (
        <div
          data-testid="compute-sam-msg"
          style={{ color: 'red', marginTop: '8px' }}
        >
          ✗ {message || 'Error'}
        </div>
      )}
    </div>
  )
}
