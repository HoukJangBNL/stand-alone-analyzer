import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useParams, useNavigate } from 'react-router-dom'
import { Toaster } from 'sonner'
import { ComputeTab } from './pages/ComputeTab'
import { LoginPage } from './pages/LoginPage'
import { SignupPage } from './pages/SignupPage'
import { AdminPage } from './pages/AdminPage'
import { Sidebar } from '@/components/Sidebar'
import { RequireAuth } from '@/components/auth/RequireAuth'
import { RequireRole } from '@/components/auth/RequireRole'
import { useProjectStore } from '@/state/projectSlice'
import { useCurrentUser } from '@/hooks/useCurrentUser'

const SelectorTab = lazy(() =>
  import('@/pages/SelectorTab').then((m) => ({ default: m.SelectorTab }))
)
const ClusteringTab = lazy(() =>
  import('@/pages/ClusteringTab').then((m) => ({ default: m.ClusteringTab }))
)
const ExplorerTab = lazy(() =>
  import('@/pages/ExplorerTab').then((m) => ({ default: m.ExplorerTab }))
)

function SelectorTabRoute() {
  const { projectId } = useParams<{ projectId: string }>()
  return (
    <Suspense fallback={<div style={{ padding: 16 }}>Loading Selector tab...</div>}>
      <SelectorTab projectId={projectId || 'local'} />
    </Suspense>
  )
}

function ClusteringTabRoute() {
  const { projectId } = useParams<{ projectId: string }>()
  return (
    <Suspense fallback={<div style={{ padding: 16 }}>Loading Clustering tab...</div>}>
      <ClusteringTab projectId={projectId || 'local'} />
    </Suspense>
  )
}

function ExplorerTabRoute() {
  const { projectId } = useParams<{ projectId: string }>()
  return (
    <Suspense fallback={<div style={{ padding: 16 }}>Loading Explorer tab...</div>}>
      <ExplorerTab projectId={projectId || 'local'} />
    </Suspense>
  )
}

/**
 * Sync the URL :projectId param into the project slice when the user lands on
 * a route directly (deep link). Also navigate to the slice's active id when
 * it diverges from the URL after a sidebar selection that originated outside a
 * specific tab.
 */
function ProjectSync() {
  const { projectId } = useParams<{ projectId: string }>()
  const slice = useProjectStore((s) => s.activeProjectId)
  const setActive = useProjectStore((s) => s.setActiveProjectId)
  useEffect(() => {
    if (projectId && projectId !== slice) setActive(projectId)
  }, [projectId, slice, setActive])
  return null
}

function HomeRedirect() {
  const navigate = useNavigate()
  const slice = useProjectStore((s) => s.activeProjectId)
  useEffect(() => {
    navigate(`/projects/${slice ?? 'local'}/compute`, { replace: true })
  }, [slice, navigate])
  return null
}

function AppContent() {
  useCurrentUser()
  return (
    <div
      data-testid="app-root-layout"
      style={{
        display: 'grid',
        gridTemplateColumns: '220px 1fr',
        minHeight: '100vh',
      }}
    >
      <Sidebar />
      <main style={{ padding: '20px', minWidth: 0 }}>
        <h1>Stand-Alone Analyzer</h1>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/signup" element={<SignupPage />} />
          <Route
            path="/admin"
            element={
              <RequireAuth>
                <RequireRole role="admin">
                  <AdminPage />
                </RequireRole>
              </RequireAuth>
            }
          />
          <Route
            path="/"
            element={
              <RequireAuth>
                <HomeRedirect />
              </RequireAuth>
            }
          />
          <Route
            path="/projects/:projectId/compute"
            element={
              <RequireAuth>
                <ProjectSync />
                <ComputeTab />
              </RequireAuth>
            }
          />
          <Route
            path="/projects/:projectId/selector"
            element={
              <RequireAuth>
                <ProjectSync />
                <SelectorTabRoute />
              </RequireAuth>
            }
          />
          <Route
            path="/projects/:projectId/clustering"
            element={
              <RequireAuth>
                <ProjectSync />
                <ClusteringTabRoute />
              </RequireAuth>
            }
          />
          <Route
            path="/projects/:projectId/explorer"
            element={
              <RequireAuth>
                <ProjectSync />
                <ExplorerTabRoute />
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}

export function App() {
  return (
    <BrowserRouter>
      <Toaster
        data-testid="app-root-toaster"
        position="top-right"
        richColors
        closeButton
      />
      <AppContent />
    </BrowserRouter>
  )
}
