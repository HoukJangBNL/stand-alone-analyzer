import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ComputeTab } from './pages/ComputeTab'

export function App() {
  return (
    <BrowserRouter>
      <div style={{ padding: '20px' }}>
        <h1>Stand-Alone Analyzer</h1>
        <Routes>
          <Route path="/" element={<Navigate to="/projects/local/compute" replace />} />
          <Route path="/projects/:projectId/compute" element={<ComputeTab />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
