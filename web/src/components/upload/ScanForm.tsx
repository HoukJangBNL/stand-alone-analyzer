// web/src/components/upload/ScanForm.tsx
//
// Controlled form: parent owns the metadata via `value` + `onChange`.
// No internal "Save" submit button — UploadModal renders the dropzone in the
// same view and the global "Start upload" button is the single commit point.
import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { MaterialCombobox } from './MaterialCombobox'

export interface ScanFormValues {
  name: string
  material: string
  extra_metadata: Record<string, string>
}

interface KV {
  key: string
  value: string
}

interface Props {
  value: ScanFormValues
  onChange(values: ScanFormValues): void
  disabled?: boolean
}

function normalizeKvs(kvs: KV[]): Record<string, string> {
  const meta: Record<string, string> = {}
  for (const kv of kvs) {
    const k = kv.key.trim()
    if (k) meta[k] = kv.value
  }
  return meta
}

export function ScanForm({ value, onChange, disabled }: Props) {
  const {
    register,
    watch,
    formState: { errors },
  } = useForm<{ name: string }>({
    mode: 'onChange',
    defaultValues: { name: value.name },
  })
  const watchedName = watch('name')

  // KV editor is purely a view concern — empty-key rows can't round-trip
  // through `Record<string,string>`, so keep the raw rows local and only
  // emit the normalized record upward.
  const [kvs, setKvs] = useState<KV[]>([])

  // Mirror RHF's `name` into parent state on each keystroke.
  useEffect(() => {
    if (watchedName !== value.name) {
      onChange({ ...value, name: watchedName, extra_metadata: normalizeKvs(kvs) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchedName])

  const updateKvs = (next: KV[]) => {
    setKvs(next)
    onChange({ ...value, extra_metadata: normalizeKvs(next) })
  }

  return (
    <form
      data-testid="scan-form"
      onSubmit={(e) => e.preventDefault()}
      style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
    >
      <label style={{ fontSize: 12 }}>
        Scan name <span style={{ color: '#b91c1c' }}>*</span>
        <input
          data-testid="scan-form-name"
          {...register('name', { required: 'name required', minLength: 1 })}
          disabled={disabled}
          style={{ width: '100%' }}
        />
        {errors.name && (
          <span style={{ color: '#b91c1c', fontSize: 11 }}>{errors.name.message}</span>
        )}
      </label>

      <label style={{ fontSize: 12 }}>
        Material <span style={{ color: '#b91c1c' }}>*</span>
        <MaterialCombobox
          value={value.material}
          onChange={(m) => onChange({ ...value, material: m, extra_metadata: normalizeKvs(kvs) })}
        />
        {!value.material && (
          <span data-testid="scan-form-material-error" style={{ color: '#b91c1c', fontSize: 11 }}>
            material required
          </span>
        )}
      </label>

      <fieldset style={{ border: '1px solid #e5e7eb', padding: 8 }}>
        <legend style={{ fontSize: 12 }}>Extra metadata (optional)</legend>
        {kvs.map((kv, i) => (
          <div key={i} style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
            <input
              data-testid={`scan-form-kv-key-${i}`}
              placeholder="key"
              value={kv.key}
              onChange={(e) =>
                updateKvs(kvs.map((c, j) => (j === i ? { ...c, key: e.target.value } : c)))
              }
              style={{ flex: 1 }}
            />
            <input
              data-testid={`scan-form-kv-value-${i}`}
              placeholder="value"
              value={kv.value}
              onChange={(e) =>
                updateKvs(kvs.map((c, j) => (j === i ? { ...c, value: e.target.value } : c)))
              }
              style={{ flex: 2 }}
            />
            <button
              type="button"
              data-testid={`scan-form-kv-remove-${i}`}
              onClick={() => updateKvs(kvs.filter((_, j) => j !== i))}
            >
              ×
            </button>
          </div>
        ))}
        <button
          type="button"
          data-testid="scan-form-kv-add"
          onClick={() => updateKvs([...kvs, { key: '', value: '' }])}
        >
          + add row
        </button>
      </fieldset>
    </form>
  )
}
