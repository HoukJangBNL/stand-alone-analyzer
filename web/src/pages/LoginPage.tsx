// web/src/pages/LoginPage.tsx
import { useState } from 'react'
import { useAuthStore } from '@/state/authSlice'

export function LoginPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const login = useAuthStore((s) => s.login)
  const status = useAuthStore((s) => s.status)
  const error = useAuthStore((s) => s.error)

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    login(email, password)
  }

  return (
    <div style={{ maxWidth: 400, margin: '40px auto', padding: 20 }}>
      <h2>Login</h2>
      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: 16 }}>
          <label htmlFor="auth-email-input" style={{ display: 'block', marginBottom: 4 }}>
            Email
          </label>
          <input
            id="auth-email-input"
            data-testid="auth-email-input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ width: '100%', padding: 8 }}
          />
        </div>
        <div style={{ marginBottom: 16 }}>
          <label htmlFor="auth-password-input" style={{ display: 'block', marginBottom: 4 }}>
            Password
          </label>
          <input
            id="auth-password-input"
            data-testid="auth-password-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ width: '100%', padding: 8 }}
          />
        </div>
        {error && <div style={{ color: '#b91c1c', marginBottom: 16 }}>{error}</div>}
        <button
          data-testid="auth-submit"
          type="submit"
          disabled={status === 'loading'}
          style={{ padding: '8px 16px' }}
        >
          {status === 'loading' ? 'Logging in...' : 'Login'}
        </button>
      </form>
    </div>
  )
}
