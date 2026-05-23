// web/src/App.tsx
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
import { ScanPicker } from '@/components/scans/ScanPicker'

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
  const { projectId, scanId } = useParams<{ projectId: string; scanId?: string }>()
  return (
    <Suspense fallback={<div style={{ padding: 16 }}>Loading Selector tab...</div>}>
      <SelectorTab projectId={projectId || ''} scanId={scanId ? Number(scanId) : null} />
    </Suspense>
  )
}

function ClusteringTabRoute() {
  const { projectId, scanId } = useParams<{ projectId: string; scanId?: string }>()
  return (
    <Suspense fallback={<div style={{ padding: 16 }}>Loading Clustering tab...</div>}>
      <ClusteringTab projectId={projectId || ''} scanId={scanId ? Number(scanId) : null} />
    </Suspense>
  )
}

function ExplorerTabRoute() {
  const { projectId, scanId } = useParams<{ projectId: string; scanId?: string }>()
  return (
    <Suspense fallback={<div style={{ padding: 16 }}>Loading Explorer tab...</div>}>
      <ExplorerTab projectId={projectId || ''} scanId={scanId ? Number(scanId) : null} />
    </Suspense>
  )
}

/**
 * Sync URL params into the project slice. Exported so unit tests can mount it
 * inside a tiny harness without booting the whole app.
 */
export function ProjectScanSync() {
  const { projectId, scanId } = useParams<{ projectId: string; scanId?: string }>()
  const sliceProject = useProjectStore((s) => s.activeProjectId)
  const sliceScan = useProjectStore((s) => s.activeScanId)
  const setProject = useProjectStore((s) => s.setActiveProjectId)
  const setScan = useProjectStore((s) => s.setActiveScanId)

  useEffect(() => {
    if (projectId && projectId !== sliceProject) setProject(projectId)
  }, [projectId, sliceProject, setProject])

  useEffect(() => {
    const next = scanId ? Number(scanId) : null
    if (next !== sliceScan) setScan(next)
  }, [scanId, sliceScan, setScan])

  return null
}

/**
 * Home → most-recent project's most-recent scan if any, else first project's
 * empty state, else stay at /projects (Sidebar will prompt to create one).
 * Persistence is in-memory only — Sidebar's `useQuery` is the source of truth.
 */
function HomeRedirect() {
  const navigate = useNavigate()
  const slice = useProjectStore((s) => s.activeProjectId)
  useEffect(() => {
    if (slice) {
      navigate(`/projects/${slice}`, { replace: true })
    }
  }, [slice, navigate])
  return <p style={{ padding: 16 }}>프로젝트를 선택하거나 만들어주세요.</p>
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

          {/* Empty-state routes (project picked but no scan) */}
          <Route
            path="/projects/:projectId"
            element={
              <RequireAuth>
                <ProjectScanSync />
                <ScanPicker />
                <ComputeTab />
              </RequireAuth>
            }
          />

          {/* Per-scan tab routes */}
          <Route
            path="/projects/:projectId/scans/:scanId/compute"
            element={
              <RequireAuth>
                <ProjectScanSync />
                <ScanPicker />
                <ComputeTab />
              </RequireAuth>
            }
          />
          <Route
            path="/projects/:projectId/scans/:scanId/selector"
            element={
              <RequireAuth>
                <ProjectScanSync />
                <ScanPicker />
                <SelectorTabRoute />
              </RequireAuth>
            }
          />
          <Route
            path="/projects/:projectId/scans/:scanId/clustering"
            element={
              <RequireAuth>
                <ProjectScanSync />
                <ScanPicker />
                <ClusteringTabRoute />
              </RequireAuth>
            }
          />
          <Route
            path="/projects/:projectId/scans/:scanId/explorer"
            element={
              <RequireAuth>
                <ProjectScanSync />
                <ScanPicker />
                <ExplorerTabRoute />
              </RequireAuth>
            }
          />

          {/*
           * Legacy routes kept ONLY to redirect; they clear `activeScanId` in
           * the slice but force the user through the picker (D6).
           */}
          <Route
            path="/projects/:projectId/:tab"
            element={
              <RequireAuth>
                <ProjectScanSync />
                <ScanPicker />
                <ComputeTab />
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
