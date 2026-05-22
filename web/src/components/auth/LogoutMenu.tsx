// web/src/components/auth/LogoutMenu.tsx
import { useAuthStore } from '@/state/authSlice'

export function LogoutMenu() {
  const currentUser = useAuthStore((s) => s.currentUser)
  const logout = useAuthStore((s) => s.logout)

  if (!currentUser) {
    return null
  }

  return (
    <div
      data-testid="auth-logout-menu"
      style={{
        padding: 12,
        borderTop: '1px solid #e5e7eb',
        marginTop: 'auto',
      }}
    >
      <div style={{ fontSize: 12, marginBottom: 8 }}>{currentUser.email}</div>
      <div style={{ fontSize: 11, color: '#6b7280', marginBottom: 8 }}>
        Role: {currentUser.role}
      </div>
      <button
        data-testid="auth-logout-button"
        type="button"
        onClick={() => logout()}
        style={{
          width: '100%',
          padding: '6px 12px',
          fontSize: 12,
          cursor: 'pointer',
        }}
      >
        Logout
      </button>
    </div>
  )
}
