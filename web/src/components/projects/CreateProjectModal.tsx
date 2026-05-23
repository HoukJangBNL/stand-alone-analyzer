// web/src/components/projects/CreateProjectModal.tsx
import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { useForm } from 'react-hook-form'
import { createProject, type Project } from '@/api/projects'

interface FormValues {
  name: string
  description: string
}

interface Props {
  open: boolean
  onClose(): void
  onCreated(project: Project): void
}

export function CreateProjectModal({ open, onClose, onCreated }: Props) {
  const qc = useQueryClient()
  const { register, handleSubmit, reset, watch, formState: { errors } } = useForm<FormValues>({
    defaultValues: { name: '', description: '' },
  })
  const name = watch('name')
  const [submitError, setSubmitError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      reset({ name: '', description: '' })
      setSubmitError(null)
    }
  }, [open, reset])

  const mut = useMutation({
    mutationFn: (vals: FormValues) =>
      createProject({
        name: vals.name.trim(),
        ...(vals.description.trim() ? { description: vals.description.trim() } : {}),
      }),
    onSuccess: (project) => {
      qc.invalidateQueries({ queryKey: ['projects', 'list'] })
      toast.success(`Project "${project.name}" created`)
      onCreated(project)
      onClose()
    },
    onError: (e: unknown) => {
      const msg = (e as { message?: string })?.message ?? 'createProject failed'
      setSubmitError(msg)
    },
  })

  if (!open) return null

  return (
    <div
      data-testid="create-project-modal-overlay"
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        data-testid="create-project-modal"
        style={{
          background: 'white', borderRadius: 6, padding: 16,
          width: 420, maxWidth: '90vw',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <h3 style={{ margin: 0 }}>새 프로젝트</h3>
          <button data-testid="create-project-modal-close" onClick={onClose}>닫기</button>
        </div>

        <form
          data-testid="create-project-modal-form"
          onSubmit={handleSubmit((v) => { setSubmitError(null); mut.mutate(v) })}
          style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
        >
          <label style={{ fontSize: 12 }}>
            Name <span style={{ color: '#b91c1c' }}>*</span>
            <input
              data-testid="create-project-modal-name"
              type="text"
              {...register('name', { required: true, minLength: 1, maxLength: 200 })}
              style={{ width: '100%' }}
              autoFocus
            />
            {errors.name && (
              <span style={{ color: '#b91c1c', fontSize: 11 }}>name required (1-200 chars)</span>
            )}
          </label>

          <label style={{ fontSize: 12 }}>
            Description (optional)
            <input
              data-testid="create-project-modal-description"
              type="text"
              {...register('description', { maxLength: 1000 })}
              style={{ width: '100%' }}
            />
          </label>

          {submitError && (
            <p data-testid="create-project-modal-error" style={{ color: '#b91c1c', fontSize: 12, margin: 0 }}>
              {submitError}
            </p>
          )}

          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" onClick={onClose}>취소</button>
            <button
              data-testid="create-project-modal-submit"
              type="submit"
              disabled={!name.trim() || mut.isPending}
            >
              {mut.isPending ? 'Creating...' : '만들기'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
