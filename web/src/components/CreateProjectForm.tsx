// web/src/components/CreateProjectForm.tsx
import { useState } from 'react'
import type { FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { createProject, type CreateProjectBody, type ProjectHandle } from '@/api/projects'
import { useProjectStore } from '@/state/projectSlice'

interface Props {
  onCreated?(handle: ProjectHandle): void
}

export function CreateProjectForm({ onCreated }: Props) {
  const qc = useQueryClient()
  const setActive = useProjectStore((s) => s.setActiveProjectId)
  const [analysisFolder, setAnalysisFolder] = useState('')
  const [rawImages, setRawImages] = useState('')
  const [annotations, setAnnotations] = useState('')

  const m = useMutation({
    mutationFn: (body: CreateProjectBody) => createProject(body),
    onSuccess: (handle) => {
      qc.invalidateQueries({ queryKey: ['projects', 'list'] })
      setActive(handle.project_id)
      toast.success(`Created project ${handle.project_id}`)
      onCreated?.(handle)
    },
    onError: (e: unknown) => {
      const msg = (e as { message?: string })?.message ?? 'Create failed'
      toast.error(msg)
    },
  })

  const onSubmit = (ev: FormEvent) => {
    ev.preventDefault()
    m.mutate({
      analysis_folder: analysisFolder,
      raw_images_dir: rawImages || null,
      annotations_path: annotations || null,
    })
  }

  return (
    <form
      data-testid="sidebar-create-form"
      onSubmit={onSubmit}
      style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: 8 }}
    >
      <label style={{ fontSize: 12 }}>
        Analysis folder
        <input
          data-testid="sidebar-create-analysis-folder"
          type="text"
          value={analysisFolder}
          onChange={(e) => setAnalysisFolder(e.target.value)}
          required
          style={{ width: '100%' }}
        />
      </label>
      <label style={{ fontSize: 12 }}>
        Raw images dir
        <input
          data-testid="sidebar-create-raw-images"
          type="text"
          value={rawImages}
          onChange={(e) => setRawImages(e.target.value)}
          style={{ width: '100%' }}
        />
      </label>
      <label style={{ fontSize: 12 }}>
        Annotations.json
        <input
          data-testid="sidebar-create-annotations"
          type="text"
          value={annotations}
          onChange={(e) => setAnnotations(e.target.value)}
          style={{ width: '100%' }}
        />
      </label>
      <button
        data-testid="sidebar-create-submit"
        type="submit"
        disabled={m.isPending || !analysisFolder}
      >
        {m.isPending ? 'Creating...' : 'Create project'}
      </button>
    </form>
  )
}
