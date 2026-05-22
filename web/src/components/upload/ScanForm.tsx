// web/src/components/upload/ScanForm.tsx
import { useForm, type SubmitHandler } from 'react-hook-form'
import { useState } from 'react'
import { MaterialCombobox } from './MaterialCombobox'

export interface ScanFormValues {
  name: string
  material: string
  image_count: number
  extra_metadata: Record<string, string>
}

interface KV {
  key: string
  value: string
}

interface Props {
  defaultExpectedCount?: number
  onSubmit(values: ScanFormValues): void
  disabled?: boolean
}

export function ScanForm({ defaultExpectedCount, onSubmit, disabled }: Props) {
  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<{
    name: string
    image_count: number
  }>({
    defaultValues: {
      name: '',
      image_count: defaultExpectedCount ?? 1,
    },
  })
  const [material, setMaterial] = useState('')
  const [kvs, setKvs] = useState<KV[]>([])

  const handle: SubmitHandler<{ name: string; image_count: number }> = (vals) => {
    if (!material) return // MaterialCombobox shows its own UI; bail silently
    const meta: Record<string, string> = {}
    for (const kv of kvs) {
      const k = kv.key.trim()
      if (k) meta[k] = kv.value
    }
    onSubmit({
      name: vals.name.trim(),
      material,
      image_count: Number(vals.image_count),
      extra_metadata: meta,
    })
  }

  return (
    <form
      data-testid="scan-form"
      onSubmit={handleSubmit(handle)}
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
        <MaterialCombobox value={material} onChange={setMaterial} />
        {!material && (
          <span data-testid="scan-form-material-error" style={{ color: '#b91c1c', fontSize: 11 }}>
            material required
          </span>
        )}
      </label>

      <label style={{ fontSize: 12 }}>
        Image count <span style={{ color: '#b91c1c' }}>*</span>
        <input
          data-testid="scan-form-image-count"
          type="number"
          min={1}
          {...register('image_count', { required: true, valueAsNumber: true, min: 1 })}
          disabled={disabled}
          style={{ width: '100%' }}
        />
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
                setKvs((cur) => cur.map((c, j) => (j === i ? { ...c, key: e.target.value } : c)))
              }
              style={{ flex: 1 }}
            />
            <input
              data-testid={`scan-form-kv-value-${i}`}
              placeholder="value"
              value={kv.value}
              onChange={(e) =>
                setKvs((cur) => cur.map((c, j) => (j === i ? { ...c, value: e.target.value } : c)))
              }
              style={{ flex: 2 }}
            />
            <button
              type="button"
              data-testid={`scan-form-kv-remove-${i}`}
              onClick={() => setKvs((cur) => cur.filter((_, j) => j !== i))}
            >
              ×
            </button>
          </div>
        ))}
        <button
          type="button"
          data-testid="scan-form-kv-add"
          onClick={() => setKvs((cur) => [...cur, { key: '', value: '' }])}
        >
          + add row
        </button>
      </fieldset>

      <button data-testid="scan-form-submit" type="submit" disabled={disabled || !material}>
        Save scan metadata
      </button>
    </form>
  )
}
