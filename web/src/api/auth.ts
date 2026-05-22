// web/src/api/auth.ts
/**
 * Auth API client — login/logout/me/exchangeCode.
 *
 * NOTE: loginWithPassword and signUp are STUBS pending Cognito SDK integration (W6.1 follow-up).
 * Production login flow uses OAuth code-exchange via /auth/callback (exchangeCode below).
 * The UI scaffolding type-checks and passes vitest, but calling these stubs at runtime throws NotImplementedError.
 */

import { ApiError } from '@/api/selector'

export type UserRole = 'member' | 'reader' | 'operator' | 'admin'

export interface CurrentUser {
  id: string
  email: string
  role: UserRole
  email_verified: boolean
  cognito_sub?: string
}

export interface LoginResult {
  id_token: string
  expires_in: number
  user: CurrentUser
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let envelope: any = null
    try {
      envelope = await resp.json()
    } catch {
      throw new ApiError(resp.status, 'http_error', `HTTP ${resp.status}`, null)
    }
    const err = envelope?.error ?? {}
    throw new ApiError(
      resp.status,
      err.code ?? 'http_error',
      err.message ?? `HTTP ${resp.status}`,
      err.details ?? null,
      err.request_id
    )
  }
  return (await resp.json()) as T
}

/**
 * STUB: Direct password login (Cognito SDK integration pending).
 * UI scaffolding only — throws at runtime.
 */
export async function loginWithPassword(
  _email: string,
  _password: string
): Promise<LoginResult> {
  throw new Error(
    'NotImplementedError: Cognito SDK integration pending — see W6.1 follow-up'
  )
}

/**
 * STUB: User sign-up (Cognito SDK integration pending).
 */
export async function signUp(
  _email: string,
  _password: string
): Promise<{ user_id: string }> {
  throw new Error(
    'NotImplementedError: Cognito SDK integration pending — see W6.1 follow-up'
  )
}

/**
 * STUB: Confirm sign-up with verification code.
 */
export async function confirmSignup(_email: string, _code: string): Promise<void> {
  throw new Error(
    'NotImplementedError: Cognito SDK integration pending — see W6.1 follow-up'
  )
}

/**
 * Exchange OAuth authorization code for tokens (production login flow).
 */
export async function exchangeCode(code: string, redirectUri: string): Promise<LoginResult> {
  const resp = await fetch('/api/v1/auth/callback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    credentials: 'include',
    body: JSON.stringify({ code, redirect_uri: redirectUri }),
  })
  return unwrap<LoginResult>(resp)
}

/**
 * Fetch current user (hydrate on mount).
 */
export async function fetchCurrentUser(): Promise<CurrentUser> {
  const resp = await fetch('/api/v1/auth/me', {
    headers: { Accept: 'application/json' },
    credentials: 'include',
  })
  return unwrap<CurrentUser>(resp)
}

/**
 * Logout (clears HttpOnly refresh cookie).
 */
export async function logout(): Promise<void> {
  const resp = await fetch('/api/v1/auth/logout', {
    method: 'POST',
    credentials: 'include',
  })
  if (!resp.ok) {
    throw new ApiError(resp.status, 'logout_failed', `HTTP ${resp.status}`, null)
  }
}
