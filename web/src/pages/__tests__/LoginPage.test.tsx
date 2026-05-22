import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LoginPage } from '@/pages/LoginPage'
import { useAuthStore, resetAuthStore } from '@/state/authSlice'

vi.mock('@/api/auth', () => ({
  loginWithPassword: vi.fn(async () => ({ id_token: 'tok', expires_in: 3600, user: { id: 'u', email: 'a@b', role: 'member', email_verified: true } })),
  logout: vi.fn(async () => undefined),
  fetchCurrentUser: vi.fn(async () => ({ id: 'u', email: 'a@b', role: 'member', email_verified: true })),
}))

describe('<LoginPage>', () => {
  beforeEach(() => resetAuthStore())
  it('submits email + password to slice', () => {
    const spy = vi.spyOn(useAuthStore.getState(), 'login')
    render(<LoginPage />)
    fireEvent.change(screen.getByTestId('auth-email-input'), { target: { value: 'a@b' } })
    fireEvent.change(screen.getByTestId('auth-password-input'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByTestId('auth-submit'))
    expect(spy).toHaveBeenCalledWith('a@b', 'pw')
  })
})
