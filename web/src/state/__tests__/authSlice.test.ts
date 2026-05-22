import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useAuthStore, resetAuthStore } from '@/state/authSlice'

vi.mock('@/api/auth', () => ({
  loginWithPassword: vi.fn(async () => ({ id_token: 'tok', expires_in: 3600, user: { id: 'u', email: 'a@b', role: 'member', email_verified: true } })),
  logout: vi.fn(async () => undefined),
  fetchCurrentUser: vi.fn(async () => ({ id: 'u', email: 'a@b', role: 'member', email_verified: true })),
}))

describe('authSlice', () => {
  beforeEach(() => resetAuthStore())

  it('login transitions idle→loading→authenticated', async () => {
    const p = useAuthStore.getState().login('a@b', 'p')
    expect(useAuthStore.getState().status).toBe('loading')
    await p
    expect(useAuthStore.getState().status).toBe('authenticated')
    expect(useAuthStore.getState().currentUser?.email).toBe('a@b')
  })

  it('logout clears state', async () => {
    await useAuthStore.getState().login('a@b', 'p')
    await useAuthStore.getState().logout()
    expect(useAuthStore.getState().currentUser).toBeNull()
    expect(useAuthStore.getState().status).toBe('idle')
  })
})
