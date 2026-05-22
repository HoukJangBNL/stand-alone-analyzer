import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { RequireRole } from '@/components/auth/RequireRole'
import { useAuthStore, resetAuthStore } from '@/state/authSlice'

describe('<RequireRole>', () => {
  beforeEach(() => resetAuthStore())

  it('hides children when user role is insufficient', () => {
    useAuthStore.setState({
      status: 'authenticated',
      currentUser: { id: 'u1', email: 'a@b', role: 'member', email_verified: true },
      idToken: 'tok',
      error: null,
    })
    render(
      <RequireRole role="admin">
        <div data-testid="admin-content">Admin Only</div>
      </RequireRole>
    )
    expect(screen.queryByTestId('admin-content')).toBeNull()
  })

  it('shows children when user role is sufficient', () => {
    useAuthStore.setState({
      status: 'authenticated',
      currentUser: { id: 'u1', email: 'a@b', role: 'admin', email_verified: true },
      idToken: 'tok',
      error: null,
    })
    render(
      <RequireRole role="admin">
        <div data-testid="admin-content">Admin Only</div>
      </RequireRole>
    )
    expect(screen.getByTestId('admin-content')).toBeDefined()
  })

  it('respects role hierarchy (operator >= reader)', () => {
    useAuthStore.setState({
      status: 'authenticated',
      currentUser: { id: 'u1', email: 'a@b', role: 'operator', email_verified: true },
      idToken: 'tok',
      error: null,
    })
    render(
      <RequireRole role="reader">
        <div data-testid="reader-content">Reader+</div>
      </RequireRole>
    )
    expect(screen.getByTestId('reader-content')).toBeDefined()
  })
})
