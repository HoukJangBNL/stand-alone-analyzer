// web/src/components/run/PipelineParamsForm.tsx
/**
 * P5.3 — PipelineParamsForm.
 *
 * Five collapsible sections (one per step) backing a single PipelineBody.
 * Background-section dirty tracking emits a callback so P5.4 (ComputeTab) can
 * prompt the user to re-run downstream steps when the cascade rule fires.
 *
 * SAM now uses the AMI-baked fine-tuned model — no weights_path needed.
 *
 * No persistence: form values live in component state only (Plan §P5.1
 * Option B).
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { PipelineBody } from '@/hooks/usePipelineProgress'

interface FormValues {
  thumbnails: {
    raw_ext: string
    quality: number
    force_recompute: boolean
  }
  background: {
    seed: number
    max_images: number
    gaussian_sigma: number
    method: string
  }
  sam: {
    device: string | null
  }
  domain_stats: {
    repr_mode: string
    raw_ext: string
  }
  domain_proximity: {
    r_max_px: number
    min_area_px: number
    max_area_px: number | null
    d_touch_px: number
    pixel_size_um: number
    link_distance_um: number
    workers: number
  }
}

const DEFAULTS: FormValues = {
  thumbnails: { raw_ext: '.png', quality: 80, force_recompute: false },
  background: {
    seed: 0,
    max_images: 100,
    gaussian_sigma: 10.0,
    method: 'median',
  },
  sam: { device: null },
  domain_stats: { repr_mode: 'median', raw_ext: '.png' },
  domain_proximity: {
    r_max_px: 200,
    min_area_px: 10,
    max_area_px: null,
    d_touch_px: 2,
    pixel_size_um: 0.5,
    link_distance_um: 5,
    workers: 4,
  },
}

function mergeDefaults(initial: Partial<PipelineBody> | undefined): FormValues {
  return {
    thumbnails: { ...DEFAULTS.thumbnails, ...(initial?.thumbnails ?? {}) },
    background: { ...DEFAULTS.background, ...(initial?.background ?? {}) },
    sam: { ...DEFAULTS.sam, ...(initial?.sam ?? {}) },
    domain_stats: {
      ...DEFAULTS.domain_stats,
      ...(initial?.domain_stats ?? {}),
    },
    domain_proximity: {
      ...DEFAULTS.domain_proximity,
      ...(initial?.domain_proximity ?? {}),
    },
  }
}

function bgEqual(a: FormValues['background'], b: FormValues['background']): boolean {
  return (
    a.seed === b.seed &&
    a.max_images === b.max_images &&
    a.gaussian_sigma === b.gaussian_sigma &&
    a.method === b.method
  )
}

interface Props {
  initialValues?: Partial<PipelineBody>
  onSubmit: (body: PipelineBody) => void
  onBackgroundDirty?: (dirty: boolean) => void
  onSavedValuesChange?: (body: PipelineBody) => void
  isRunning?: boolean
}

export function PipelineParamsForm({
  initialValues,
  onSubmit,
  onBackgroundDirty,
  onSavedValuesChange,
  isRunning = false,
}: Props) {
  const initial = useMemo(() => mergeDefaults(initialValues), [initialValues])
  const [values, setValues] = useState<FormValues>(initial)
  const [bgDirty, setBgDirty] = useState(false)

  // Track background dirtiness against initial.background.
  useEffect(() => {
    const dirty = !bgEqual(values.background, initial.background)
    if (dirty !== bgDirty) {
      setBgDirty(dirty)
      onBackgroundDirty?.(dirty)
    }
  }, [values.background, initial.background, bgDirty, onBackgroundDirty])

  // Surface current values to interested parents (e.g., for the cascade dialog).
  useEffect(() => {
    onSavedValuesChange?.(values as PipelineBody)
    // We intentionally fire on every change; consumers should debounce if needed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [values])

  const update = useCallback(
    <K extends keyof FormValues>(section: K, patch: Partial<FormValues[K]>) => {
      setValues((prev) => ({
        ...prev,
        [section]: { ...prev[section], ...patch },
      }))
    },
    []
  )

  const handleRun = useCallback(() => {
    onSubmit(values as PipelineBody)
  }, [onSubmit, values])

  return (
    <div data-testid="pipeline-form" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Thumbnails */}
      <details data-testid="pipeline-form-section-thumbnails">
        <summary>Thumbnails</summary>
        <div style={{ padding: '8px 12px', display: 'grid', gap: 6 }}>
          <label>
            raw_ext
            <input
              data-testid="pipeline-form-thumbnails-raw-ext"
              type="text"
              value={values.thumbnails.raw_ext}
              onChange={(e) => update('thumbnails', { raw_ext: e.target.value })}
            />
            <small> Default: .png</small>
          </label>
          <label>
            quality
            <input
              data-testid="pipeline-form-thumbnails-quality"
              type="number"
              min={1}
              max={100}
              value={values.thumbnails.quality}
              onChange={(e) =>
                update('thumbnails', { quality: Number(e.target.value) })
              }
            />
            <small> Default: 80</small>
          </label>
          <label>
            <input
              data-testid="pipeline-form-thumbnails-force-recompute"
              type="checkbox"
              checked={values.thumbnails.force_recompute}
              onChange={(e) =>
                update('thumbnails', { force_recompute: e.target.checked })
              }
            />
            force_recompute
          </label>
        </div>
      </details>

      {/* Background */}
      <details
        data-testid="pipeline-form-section-background"
        data-dirty={bgDirty ? 'true' : 'false'}
      >
        <summary>Background</summary>
        <div style={{ padding: '8px 12px', display: 'grid', gap: 6 }}>
          <label>
            seed
            <input
              data-testid="pipeline-form-background-seed"
              type="number"
              value={values.background.seed}
              onChange={(e) =>
                update('background', { seed: Number(e.target.value) })
              }
            />
            <small> Default: 0</small>
          </label>
          <label>
            max_images
            <input
              data-testid="pipeline-form-background-max-images"
              type="number"
              min={1}
              value={values.background.max_images}
              onChange={(e) =>
                update('background', { max_images: Number(e.target.value) })
              }
            />
            <small> Default: 100</small>
          </label>
          <label>
            gaussian_sigma
            <input
              data-testid="pipeline-form-background-gaussian-sigma"
              type="number"
              step="0.1"
              value={values.background.gaussian_sigma}
              onChange={(e) =>
                update('background', {
                  gaussian_sigma: Number(e.target.value),
                })
              }
            />
            <small> Default: 10.0</small>
          </label>
          <label>
            method
            <input
              data-testid="pipeline-form-background-method"
              type="text"
              value={values.background.method}
              onChange={(e) =>
                update('background', { method: e.target.value })
              }
            />
            <small> Default: median</small>
          </label>
        </div>
      </details>

      {/* SAM */}
      <details data-testid="pipeline-form-section-sam">
        <summary>SAM</summary>
        <div style={{ padding: '8px 12px', display: 'grid', gap: 6 }}>
          <p style={{ fontSize: '0.9em', color: '#555', margin: 0 }}>
            Uses the configured fine-tuned model from the AMI.
          </p>
          <label>
            device
            <input
              data-testid="pipeline-form-sam-device"
              type="text"
              placeholder="(auto)"
              value={values.sam.device ?? ''}
              onChange={(e) =>
                update('sam', {
                  device: e.target.value === '' ? null : e.target.value,
                })
              }
            />
            <small> Empty = server auto-detects.</small>
          </label>
        </div>
      </details>

      {/* Domain Stats */}
      <details data-testid="pipeline-form-section-domain-stats">
        <summary>Domain Stats</summary>
        <div style={{ padding: '8px 12px', display: 'grid', gap: 6 }}>
          <label>
            repr_mode
            <input
              data-testid="pipeline-form-domain-stats-repr-mode"
              type="text"
              value={values.domain_stats.repr_mode}
              onChange={(e) =>
                update('domain_stats', { repr_mode: e.target.value })
              }
            />
            <small> Default: median</small>
          </label>
          <label>
            raw_ext
            <input
              data-testid="pipeline-form-domain-stats-raw-ext"
              type="text"
              value={values.domain_stats.raw_ext}
              onChange={(e) =>
                update('domain_stats', { raw_ext: e.target.value })
              }
            />
            <small> Default: .png</small>
          </label>
        </div>
      </details>

      {/* Domain Proximity */}
      <details data-testid="pipeline-form-section-domain-proximity">
        <summary>Domain Proximity</summary>
        <div style={{ padding: '8px 12px', display: 'grid', gap: 6 }}>
          <label>
            r_max_px
            <input
              data-testid="pipeline-form-domain-proximity-r-max-px"
              type="number"
              step="0.1"
              value={values.domain_proximity.r_max_px}
              onChange={(e) =>
                update('domain_proximity', {
                  r_max_px: Number(e.target.value),
                })
              }
            />
            <small> Default: 200</small>
          </label>
          <label>
            min_area_px
            <input
              data-testid="pipeline-form-domain-proximity-min-area-px"
              type="number"
              value={values.domain_proximity.min_area_px}
              onChange={(e) =>
                update('domain_proximity', {
                  min_area_px: Number(e.target.value),
                })
              }
            />
            <small> Default: 10</small>
          </label>
          <label>
            max_area_px
            <input
              data-testid="pipeline-form-domain-proximity-max-area-px"
              type="number"
              placeholder="(none)"
              value={values.domain_proximity.max_area_px ?? ''}
              onChange={(e) =>
                update('domain_proximity', {
                  max_area_px:
                    e.target.value === '' ? null : Number(e.target.value),
                })
              }
            />
            <small> Default: none</small>
          </label>
          <label>
            d_touch_px
            <input
              data-testid="pipeline-form-domain-proximity-d-touch-px"
              type="number"
              step="0.1"
              value={values.domain_proximity.d_touch_px}
              onChange={(e) =>
                update('domain_proximity', {
                  d_touch_px: Number(e.target.value),
                })
              }
            />
            <small> Default: 2.0</small>
          </label>
          <label>
            pixel_size_um
            <input
              data-testid="pipeline-form-domain-proximity-pixel-size-um"
              type="number"
              step="0.01"
              value={values.domain_proximity.pixel_size_um}
              onChange={(e) =>
                update('domain_proximity', {
                  pixel_size_um: Number(e.target.value),
                })
              }
            />
            <small> Default: 0.5</small>
          </label>
          <label>
            link_distance_um
            <input
              data-testid="pipeline-form-domain-proximity-link-distance-um"
              type="number"
              step="0.1"
              value={values.domain_proximity.link_distance_um}
              onChange={(e) =>
                update('domain_proximity', {
                  link_distance_um: Number(e.target.value),
                })
              }
            />
            <small> Default: 5.0</small>
          </label>
          <label>
            workers
            <input
              data-testid="pipeline-form-domain-proximity-workers"
              type="number"
              min={1}
              value={values.domain_proximity.workers}
              onChange={(e) =>
                update('domain_proximity', {
                  workers: Number(e.target.value),
                })
              }
            />
            <small> Default: 4</small>
          </label>
        </div>
      </details>

      <div style={{ marginTop: 8 }}>
        <button
          type="button"
          data-testid="pipeline-form-run"
          onClick={handleRun}
          disabled={isRunning}
        >
          ▶ Run pipeline
        </button>
      </div>
    </div>
  )
}
