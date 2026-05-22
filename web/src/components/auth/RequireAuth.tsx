// web/src/components/auth/RequireAuth.tsx
import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@/state/authSlice'

interface RequireAuthProps {
  children: React.ReactNode
}

export function RequireAuth({ children }: RequireAuthProps) {
  const currentUser = useAuthStore((s) => s.currentUser)

  if (!currentUser) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
