// web/src/components/Sidebar.tsx
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { listProjects, type Project } from '@/api/projects'
import { useProjectStore } from '@/state/projectSlice'
import { CreateProjectModal } from '@/components/projects/CreateProjectModal'
import { LogoutMenu } from './auth/LogoutMenu'

export function Sidebar() {
  const navigate = useNavigate()
  const activeId = useProjectStore((s) => s.activeProjectId)
  const setActive = useProjectStore((s) => s.setActiveProjectId)
  const [openCreate, setOpenCreate] = useState(false)

  const list = useQuery<Project[]>({
    queryKey: ['projects', 'list'],
    queryFn: listProjects,
    staleTime: 5_000,
    retry: false,
  })

  const projects = list.data ?? []
  const empty = !list.isLoading && projects.length === 0

  const onSelect = (id: string) => {
    setActive(id)
    navigate(`/projects/${id}`)
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
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <h2 style={{ margin: 0, fontSize: 14 }}>Projects</h2>
        <button
          data-testid="sidebar-new-project-btn"
          type="button"
          onClick={() => setOpenCreate(true)}
          aria-label="새 프로젝트"
          style={{ padding: '2px 8px' }}
        >
          +
        </button>
      </div>

      {list.isLoading && <p style={{ fontSize: 12, color: '#6b7280' }}>Loading...</p>}
      {list.isError && <p style={{ color: '#b91c1c', fontSize: 12 }}>Failed to load projects.</p>}

      {empty ? (
        <p data-testid="sidebar-empty-state" style={{ fontSize: 12, color: '#6b7280' }}>
          시작하려면 프로젝트를 만들어주세요.
        </p>
      ) : (
        <ul
          data-testid="sidebar-project-list"
          style={{ listStyle: 'none', padding: 0, margin: 0 }}
        >
          {projects.map((p) => (
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
                title={p.description ?? undefined}
              >
                {p.name}{' '}
                <span style={{ color: '#9ca3af', fontSize: 11 }}>({p.scan_count})</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      <CreateProjectModal
        open={openCreate}
        onClose={() => setOpenCreate(false)}
        onCreated={(p) => {
          setActive(p.project_id)
          navigate(`/projects/${p.project_id}`)
        }}
      />

      <LogoutMenu />
    </aside>
  )
}
