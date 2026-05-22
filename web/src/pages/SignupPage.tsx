// web/src/pages/SignupPage.tsx
import { useState } from 'react'
import { signUp, confirmSignup } from '@/api/auth'

type SignupStep = 'register' | 'confirm' | 'done'

export function SignupPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [step, setStep] = useState<SignupStep>('register')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await signUp(email, password)
      setStep('confirm')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Signup failed')
    } finally {
      setLoading(false)
    }
  }

  const handleConfirm = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await confirmSignup(email, code)
      setStep('done')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Confirmation failed')
    } finally {
      setLoading(false)
    }
  }

  if (step === 'done') {
    return (
      <div style={{ maxWidth: 400, margin: '40px auto', padding: 20 }}>
        <h2>Signup Complete</h2>
        <p>Your account has been confirmed. You can now log in.</p>
      </div>
    )
  }

  if (step === 'confirm') {
    return (
      <div style={{ maxWidth: 400, margin: '40px auto', padding: 20 }}>
        <h2>Confirm Email</h2>
        <form onSubmit={handleConfirm}>
          <div style={{ marginBottom: 16 }}>
            <label htmlFor="confirm-code-input" style={{ display: 'block', marginBottom: 4 }}>
              Verification Code
            </label>
            <input
              id="confirm-code-input"
              data-testid="confirm-code-input"
              type="text"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              style={{ width: '100%', padding: 8 }}
            />
          </div>
          {error && <div style={{ color: '#b91c1c', marginBottom: 16 }}>{error}</div>}
          <button
            data-testid="confirm-submit"
            type="submit"
            disabled={loading}
            style={{ padding: '8px 16px' }}
          >
            {loading ? 'Confirming...' : 'Confirm'}
          </button>
        </form>
      </div>
    )
  }

  return (
    <div style={{ maxWidth: 400, margin: '40px auto', padding: 20 }}>
      <h2>Sign Up</h2>
      <form onSubmit={handleSignup}>
        <div style={{ marginBottom: 16 }}>
          <label htmlFor="signup-email-input" style={{ display: 'block', marginBottom: 4 }}>
            Email
          </label>
          <input
            id="signup-email-input"
            data-testid="signup-email-input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ width: '100%', padding: 8 }}
          />
        </div>
        <div style={{ marginBottom: 16 }}>
          <label htmlFor="signup-password-input" style={{ display: 'block', marginBottom: 4 }}>
            Password
          </label>
          <input
            id="signup-password-input"
            data-testid="signup-password-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ width: '100%', padding: 8 }}
          />
        </div>
        {error && <div style={{ color: '#b91c1c', marginBottom: 16 }}>{error}</div>}
        <button
          data-testid="signup-submit"
          type="submit"
          disabled={loading}
          style={{ padding: '8px 16px' }}
        >
          {loading ? 'Signing up...' : 'Sign Up'}
        </button>
      </form>
    </div>
  )
}
