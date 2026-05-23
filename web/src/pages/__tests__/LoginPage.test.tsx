import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { LoginPage } from '@/pages/LoginPage'
import { useAuthStore, resetAuthStore } from '@/state/authSlice'

vi.mock('@/api/auth', () => ({
  loginWithPassword: vi.fn(async () => ({ id_token: 'tok', expires_in: 3600, user: { id: 'u', email: 'a@b', role: 'member', email_verified: true } })),
  logout: vi.fn(async () => undefined),
  fetchCurrentUser: vi.fn(async () => ({ id: 'u', email: 'a@b', role: 'member', email_verified: true })),
}))

function renderLoginPage() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>
  )
}

describe('<LoginPage>', () => {
  beforeEach(() => resetAuthStore())
  afterEach(() => resetAuthStore())

  it('submits email + password to slice', () => {
    const spy = vi.spyOn(useAuthStore.getState(), 'login')
    renderLoginPage()
    fireEvent.change(screen.getByTestId('auth-email-input'), { target: { value: 'a@b' } })
    fireEvent.change(screen.getByTestId('auth-password-input'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByTestId('auth-submit'))
    expect(spy).toHaveBeenCalledWith('a@b', 'pw')
  })

  it('redirects to / when already authenticated', () => {
    useAuthStore.setState({
      currentUser: { id: 'u1', email: 'x@y', role: 'admin', email_verified: true },
    })
    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<div data-testid="home-stub">home</div>} />
        </Routes>
      </MemoryRouter>
    )
    expect(screen.getByTestId('home-stub').textContent).toBe('home')
  })
})
