import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { LogoutMenu } from '@/components/auth/LogoutMenu'
import { useAuthStore, resetAuthStore } from '@/state/authSlice'

vi.mock('@/api/auth', () => ({
  loginWithPassword: vi.fn(async () => ({ id_token: 'tok', expires_in: 3600, user: { id: 'u', email: 'a@b', role: 'member', email_verified: true } })),
  logout: vi.fn(async () => undefined),
  fetchCurrentUser: vi.fn(async () => ({ id: 'u', email: 'a@b', role: 'member', email_verified: true })),
}))

describe('<LogoutMenu>', () => {
  beforeEach(() => resetAuthStore())

  it('renders user email when authenticated', () => {
    useAuthStore.setState({
      status: 'authenticated',
      currentUser: { id: 'u1', email: 'test@example.com', role: 'member', email_verified: true },
      idToken: 'tok',
      error: null,
    })
    render(<LogoutMenu />)
    expect(screen.getByText('test@example.com')).toBeDefined()
  })

  it('calls logout when logout button is clicked', () => {
    useAuthStore.setState({
      status: 'authenticated',
      currentUser: { id: 'u1', email: 'test@example.com', role: 'member', email_verified: true },
      idToken: 'tok',
      error: null,
    })
    const spy = vi.spyOn(useAuthStore.getState(), 'logout')
    render(<LogoutMenu />)
    fireEvent.click(screen.getByTestId('auth-logout-button'))
    expect(spy).toHaveBeenCalled()
  })

  it('returns null when not authenticated', () => {
    const { container } = render(<LogoutMenu />)
    expect(container.firstChild).toBeNull()
  })
})
