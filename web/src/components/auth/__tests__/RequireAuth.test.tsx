import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { RequireAuth } from '@/components/auth/RequireAuth'
import { useAuthStore, resetAuthStore } from '@/state/authSlice'

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <BrowserRouter>{children}</BrowserRouter>
}

describe('<RequireAuth>', () => {
  beforeEach(() => resetAuthStore())

  it('redirects to /login when currentUser is null', () => {
    render(
      <TestWrapper>
        <RequireAuth>
          <div data-testid="protected-content">Protected</div>
        </RequireAuth>
      </TestWrapper>
    )
    expect(screen.queryByTestId('protected-content')).toBeNull()
  })

  it('renders children when authenticated', () => {
    useAuthStore.setState({
      status: 'authenticated',
      currentUser: { id: 'u1', email: 'a@b', role: 'member', email_verified: true },
      idToken: 'tok',
      error: null,
    })
    render(
      <TestWrapper>
        <RequireAuth>
          <div data-testid="protected-content">Protected</div>
        </RequireAuth>
      </TestWrapper>
    )
    expect(screen.getByTestId('protected-content')).toBeDefined()
  })
})
