// web/src/components/upload/MaterialCombobox.tsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { fetchMaterials, createMaterial, type Material } from '@/api/materials'

interface Props {
  value: string
  onChange(name: string): void
}

export function MaterialCombobox({ value, onChange }: Props) {
  const qc = useQueryClient()
  const [input, setInput] = useState(value)
  const [open, setOpen] = useState(false)

  const list = useQuery<Material[]>({
    queryKey: ['materials', 'list'],
    queryFn: fetchMaterials,
    staleTime: 60_000,
  })

  const create = useMutation({
    mutationFn: (name: string) => createMaterial(name),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['materials', 'list'] })
      onChange(res.name)
      setInput(res.name)
      setOpen(false)
      toast.success(res.created ? `Material "${res.name}" added` : `Material "${res.name}" exists`)
    },
    onError: (e: unknown) => {
      toast.error((e as { message?: string })?.message ?? 'createMaterial failed')
    },
  })

  const matches = (list.data ?? []).filter((m) =>
    m.name.toLowerCase().includes(input.toLowerCase()),
  )
  const exact = (list.data ?? []).some((m) => m.name === input)

  return (
    <div data-testid="material-combobox-root" style={{ position: 'relative' }}>
      <input
        data-testid="material-combobox-input"
        type="text"
        value={input}
        onChange={(e) => {
          setInput(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        placeholder="Type to search or add new material"
        style={{ width: '100%' }}
      />
      {open && (
        <ul
          data-testid="material-combobox-list"
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            margin: 0,
            padding: 4,
            listStyle: 'none',
            background: 'white',
            border: '1px solid #ccc',
            maxHeight: 160,
            overflowY: 'auto',
            zIndex: 10,
          }}
        >
          {matches.map((m) => (
            <li
              key={m.name}
              data-testid={`material-combobox-option-${m.name}`}
              style={{ padding: 4, cursor: 'pointer' }}
              onClick={() => {
                onChange(m.name)
                setInput(m.name)
                setOpen(false)
              }}
            >
              {m.name}
            </li>
          ))}
          {input && !exact && (
            <li
              data-testid="material-combobox-create-row"
              style={{
                padding: 4,
                borderTop: '1px solid #eee',
                background: '#eef2ff',
              }}
            >
              <button
                data-testid="material-combobox-create-btn"
                type="button"
                disabled={create.isPending}
                onClick={() => create.mutate(input)}
                style={{
                  width: '100%',
                  padding: '6px 8px',
                  background: '#eef2ff',
                  border: '1px solid #c7d2fe',
                  borderRadius: 4,
                  fontWeight: 600,
                  color: '#3730a3',
                  cursor: create.isPending ? 'wait' : 'pointer',
                  textAlign: 'left',
                }}
              >
                <span aria-hidden="true" style={{ marginRight: 6 }}>
                  ➕
                </span>
                {create.isPending ? 'Creating...' : `Add "${input}" as new material`}
              </button>
            </li>
          )}
          {!input && matches.length === 0 && (
            <li
              data-testid="material-combobox-empty-hint"
              style={{ padding: 6, color: '#6b7280', fontSize: 12 }}
            >
              Type a name to add a new material
            </li>
          )}
        </ul>
      )}
    </div>
  )
}
