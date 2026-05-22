import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { SignupPage } from '@/pages/SignupPage'
import * as authApi from '@/api/auth'

vi.mock('@/api/auth')

describe('<SignupPage>', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders email/password fields and submit button', () => {
    render(<SignupPage />)
    expect(screen.getByTestId('signup-email-input')).toBeDefined()
    expect(screen.getByTestId('signup-password-input')).toBeDefined()
    expect(screen.getByTestId('signup-submit')).toBeDefined()
  })

  it('calls signUp on submit', async () => {
    vi.mocked(authApi.signUp).mockResolvedValue({ user_id: 'u1' })
    render(<SignupPage />)
    fireEvent.change(screen.getByTestId('signup-email-input'), { target: { value: 'a@b.com' } })
    fireEvent.change(screen.getByTestId('signup-password-input'), { target: { value: 'pass123' } })
    fireEvent.click(screen.getByTestId('signup-submit'))
    await waitFor(() => expect(authApi.signUp).toHaveBeenCalledWith('a@b.com', 'pass123'))
  })

  it('shows confirm code field after signup', async () => {
    vi.mocked(authApi.signUp).mockResolvedValue({ user_id: 'u1' })
    render(<SignupPage />)
    fireEvent.change(screen.getByTestId('signup-email-input'), { target: { value: 'a@b.com' } })
    fireEvent.change(screen.getByTestId('signup-password-input'), { target: { value: 'pass123' } })
    fireEvent.click(screen.getByTestId('signup-submit'))
    await waitFor(() => expect(screen.getByTestId('confirm-code-input')).toBeDefined())
  })

  it('calls confirmSignup when confirming', async () => {
    vi.mocked(authApi.signUp).mockResolvedValue({ user_id: 'u1' })
    vi.mocked(authApi.confirmSignup).mockResolvedValue(undefined)
    render(<SignupPage />)
    fireEvent.change(screen.getByTestId('signup-email-input'), { target: { value: 'a@b.com' } })
    fireEvent.change(screen.getByTestId('signup-password-input'), { target: { value: 'pass123' } })
    fireEvent.click(screen.getByTestId('signup-submit'))
    await waitFor(() => screen.getByTestId('confirm-code-input'))
    fireEvent.change(screen.getByTestId('confirm-code-input'), { target: { value: '123456' } })
    fireEvent.click(screen.getByTestId('confirm-submit'))
    await waitFor(() => expect(authApi.confirmSignup).toHaveBeenCalledWith('a@b.com', '123456'))
  })
})
