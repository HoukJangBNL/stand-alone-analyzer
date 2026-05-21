import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom'
import { Toaster } from 'sonner'
import { ComputeTab } from './pages/ComputeTab'

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

export function App() {
  return (
    <BrowserRouter>
      <Toaster
        data-testid="app-root-toaster"
        position="top-right"
        richColors
        closeButton
      />
      <div style={{ padding: '20px' }}>
        <h1>Stand-Alone Analyzer</h1>
        <Routes>
          <Route path="/" element={<Navigate to="/projects/local/compute" replace />} />
          <Route path="/projects/:projectId/compute" element={<ComputeTab />} />
          <Route path="/projects/:projectId/selector" element={<SelectorTabRoute />} />
          <Route path="/projects/:projectId/clustering" element={<ClusteringTabRoute />} />
          <Route path="/projects/:projectId/explorer" element={<ExplorerTabRoute />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
