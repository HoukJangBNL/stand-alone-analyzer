// web/src/components/auth/RequireAuth.tsx
import { Navigate } from 'react-router-dom'
import { useAuthStore } from '@/state/authSlice'

interface RequireAuthProps {
  children: React.ReactNode
}

export function RequireAuth({ children }: RequireAuthProps) {
  const status = useAuthStore((s) => s.status)
  const currentUser = useAuthStore((s) => s.currentUser)

  // Wait for hydration to complete before redirecting
  // (hydrate() is called by useCurrentUser in App.tsx on mount)
  if (status === 'loading') {
    return <div style={{ padding: 16 }}>Loading...</div>
  }

  if (!currentUser) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}
