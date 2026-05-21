// web/src/components/Sidebar.tsx
import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchProjects, fetchActiveProject, type ProjectHandle } from '@/api/projects'
import { useProjectStore } from '@/state/projectSlice'
import { CreateProjectForm } from './CreateProjectForm'

export function Sidebar() {
  const navigate = useNavigate()
  const activeId = useProjectStore((s) => s.activeProjectId)
  const setActive = useProjectStore((s) => s.setActiveProjectId)
  const [showCreate, setShowCreate] = useState(false)

  const list = useQuery<ProjectHandle[]>({
    queryKey: ['projects', 'list'],
    queryFn: fetchProjects,
    staleTime: 5_000,
    retry: false,
  })

  // First-load: if slice is empty and backend has no list, hydrate from /active.
  useEffect(() => {
    if (activeId !== null) return
    if (list.isLoading) return
    if (list.data && list.data.length > 0) {
      setActive(list.data[0].project_id)
      return
    }
    fetchActiveProject()
      .then((h) => setActive(h.project_id))
      .catch(() => setActive('local'))
  }, [activeId, list.data, list.isLoading, setActive])

  const onSelect = (id: string) => {
    setActive(id)
    navigate(`/projects/${id}/compute`)
  }

  return (
    <aside
      data-testid="sidebar-root"
      style={{
        borderRight: '1px solid #e5e7eb',
        padding: 12,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        overflowY: 'auto',
      }}
    >
      <h2 style={{ margin: 0, fontSize: 14 }}>Projects</h2>

      <ul
        data-testid="sidebar-project-list"
        style={{ listStyle: 'none', padding: 0, margin: 0 }}
      >
        {list.isLoading && <li>Loading...</li>}
        {list.isError && <li style={{ color: '#b91c1c' }}>Failed to load projects.</li>}
        {(list.data ?? []).map((p) => (
          <li
            key={p.project_id}
            data-testid={`sidebar-project-row-${p.project_id}`}
            style={{
              padding: 6,
              background: p.project_id === activeId ? '#eef2ff' : 'transparent',
              borderRadius: 4,
            }}
          >
            <button
              data-testid={`sidebar-project-select-${p.project_id}`}
              type="button"
              onClick={() => onSelect(p.project_id)}
              style={{
                background: 'none',
                border: 'none',
                padding: 0,
                cursor: 'pointer',
                font: 'inherit',
                textAlign: 'left',
                width: '100%',
              }}
            >
              {p.project_id}
            </button>
          </li>
        ))}
      </ul>

      <button
        data-testid="sidebar-create-toggle"
        type="button"
        onClick={() => setShowCreate((v) => !v)}
      >
        {showCreate ? 'Hide create form' : 'New project...'}
      </button>

      {showCreate && (
        <CreateProjectForm
          onCreated={(handle) => {
            setShowCreate(false)
            navigate(`/projects/${handle.project_id}/compute`)
          }}
        />
      )}
    </aside>
  )
}
