// web/src/hooks/useCurrentUser.ts
import { useEffect } from 'react'
import { useAuthStore } from '@/state/authSlice'

/**
 * Hook that hydrates current user on mount (from refresh cookie).
 */
export function useCurrentUser() {
  const hydrate = useAuthStore((s) => s.hydrate)

  useEffect(() => {
    hydrate()
  }, [])
}
