// web/src/state/authSlice.ts
import { create } from 'zustand'
import type { CurrentUser } from '@/api/auth'
import { loginWithPassword, logout as apiLogout, fetchCurrentUser } from '@/api/auth'

export type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'error'

export interface AuthState {
  status: AuthStatus
  currentUser: CurrentUser | null
  idToken: string | null
  error: string | null

  login(email: string, password: string): Promise<void>
  logout(): Promise<void>
  hydrate(): Promise<void>
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: 'idle',
  currentUser: null,
  idToken: null,
  error: null,

  async login(email, password) {
    set({ status: 'loading', error: null })
    try {
      const result = await loginWithPassword(email, password)
      set({
        status: 'authenticated',
        currentUser: result.user,
        idToken: result.id_token,
        error: null,
      })
    } catch (err) {
      set({
        status: 'error',
        error: err instanceof Error ? err.message : 'Login failed',
      })
      throw err
    }
  },

  async logout() {
    try {
      await apiLogout()
    } finally {
      set({
        status: 'idle',
        currentUser: null,
        idToken: null,
        error: null,
      })
    }
  },

  async hydrate() {
    if (get().status === 'authenticated') return
    set({ status: 'loading', error: null })
    try {
      const user = await fetchCurrentUser()
      set({
        status: 'authenticated',
        currentUser: user,
        error: null,
      })
    } catch (err) {
      set({
        status: 'idle',
        currentUser: null,
        idToken: null,
        error: null,
      })
    }
  },
}))

export function resetAuthStore(): void {
  useAuthStore.setState(
    {
      status: 'idle',
      currentUser: null,
      idToken: null,
      error: null,
    },
    false
  )
}
