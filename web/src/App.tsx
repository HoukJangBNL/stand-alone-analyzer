import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useParams } from 'react-router-dom'
import { ComputeTab } from './pages/ComputeTab'

const SelectorTab = lazy(() =>
  import('@/pages/SelectorTab').then((m) => ({ default: m.SelectorTab }))
)
const ClusteringTab = lazy(() =>
  import('@/pages/ClusteringTab').then((m) => ({ default: m.ClusteringTab }))
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

export function App() {
  return (
    <BrowserRouter>
      <div style={{ padding: '20px' }}>
        <h1>Stand-Alone Analyzer</h1>
        <Routes>
          <Route path="/" element={<Navigate to="/projects/local/compute" replace />} />
          <Route path="/projects/:projectId/compute" element={<ComputeTab />} />
          <Route path="/projects/:projectId/selector" element={<SelectorTabRoute />} />
          <Route path="/projects/:projectId/clustering" element={<ClusteringTabRoute />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
