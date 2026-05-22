// web/src/api/authHeaders.ts
import { useAuthStore } from '@/state/authSlice'

/**
 * Get Authorization header with current ID token (if authenticated).
 * Returns headers object to spread into fetch options.
 */
export function getAuthHeaders(): HeadersInit {
  const token = useAuthStore.getState().idToken
  if (token) {
    return { Authorization: `Bearer ${token}` }
  }
  return {}
}
