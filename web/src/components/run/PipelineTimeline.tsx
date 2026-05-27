// web/src/components/run/PipelineTimeline.tsx
/**
 * P5.3 — PipelineTimeline.
 *
 * Five-row indicator over a {@link PipelineState}. Each row shows a status
 * glyph (○ idle / ⏳ running / ✓ done / ✗ error), the step's pretty name, a
 * progress bar (running only), and the latest message. The currently active
 * step is bolded for at-a-glance scanning.
 *
 * Pure render — no signals, no state. Intended consumer: ComputeTab (P5.4).
 */
import type { PipelineState, StepName } from '@/hooks/usePipelineProgress'

const ORDER: StepName[] = [
  'thumbnails',
  'background',
  'sam',
  'domain_stats',
  'domain_proximity',
]

const PRETTY: Record<StepName, string> = {
  thumbnails: 'Thumbnails',
  background: 'Background',
  sam: 'SAM',
  domain_stats: 'Domain Stats',
  domain_proximity: 'Domain Proximity',
}

function statusGlyph(status: string): string {
  switch (status) {
    case 'running':
      return '⏳'
    case 'done':
      return '✓'
    case 'error':
      return '✗'
    case 'idle':
    default:
      return '○'
  }
}

function statusColor(status: string): string | undefined {
  switch (status) {
    case 'running':
      return '#1e6fcc'
    case 'done':
      return '#2a8f3f'
    case 'error':
      return '#c0392b'
    default:
      return '#888'
  }
}

interface Props {
  state: PipelineState
}

export function PipelineTimeline({ state }: Props) {
  const terminal = state.phase === 'done' || state.phase === 'error'

  return (
    <div
      data-testid="pipeline-timeline"
      style={{
        border: '1px solid #ddd',
        borderRadius: 4,
        padding: '8px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      {ORDER.map((step) => {
        const slug = step.replace(/_/g, '-')
        const s = state.steps[step]
        const isCurrent = state.currentStep === step
        const showProgress = !terminal && s.status === 'running'
        const pctInt = Math.round((s.pct ?? 0) * 100)

        return (
          <div
            key={step}
            data-testid={`pipeline-timeline-row-${slug}`}
            style={{
              display: 'grid',
              gridTemplateColumns: '24px 160px 1fr',
              gap: 8,
              alignItems: 'center',
              fontWeight: isCurrent ? 600 : 400,
            }}
          >
            <span
              data-testid={`pipeline-timeline-row-${slug}-status`}
              data-status={s.status}
              style={{ color: statusColor(s.status) }}
              aria-label={`${PRETTY[step]} ${s.status}`}
            >
              {statusGlyph(s.status)}
            </span>
            <span>{PRETTY[step]}</span>
            <div>
              {showProgress && (
                <>
                  <progress
                    value={s.pct}
                    max={1}
                    style={{ width: '100%' }}
                  />
                  <span
                    data-testid={`pipeline-timeline-row-${slug}-pct`}
                    style={{ fontSize: '0.85em', color: '#555' }}
                  >
                    {pctInt}%
                  </span>
                </>
              )}
              {s.message && (
                <div
                  data-testid={`pipeline-timeline-row-${slug}-msg`}
                  style={{
                    fontSize: '0.85em',
                    color: s.status === 'error' ? '#c0392b' : '#555',
                  }}
                >
                  {s.message}
                </div>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
