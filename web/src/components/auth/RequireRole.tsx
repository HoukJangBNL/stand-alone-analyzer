// web/src/components/auth/RequireRole.tsx
import type { UserRole } from '@/api/auth'
import { useAuthStore } from '@/state/authSlice'

interface RequireRoleProps {
  role: UserRole
  children: React.ReactNode
}

const ROLE_HIERARCHY: Record<UserRole, number> = {
  member: 0,
  reader: 1,
  operator: 2,
  admin: 3,
}

export function RequireRole({ role, children }: RequireRoleProps) {
  const currentUser = useAuthStore((s) => s.currentUser)

  if (!currentUser) {
    return null
  }

  const userLevel = ROLE_HIERARCHY[currentUser.role]
  const requiredLevel = ROLE_HIERARCHY[role]

  if (userLevel < requiredLevel) {
    return null
  }

  return <>{children}</>
}
