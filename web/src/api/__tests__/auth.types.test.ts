import { expectTypeOf, describe, it } from 'vitest'
import type { LoginResult, CurrentUser } from '@/api/auth'

describe('auth types', () => {
  it('LoginResult exposes id_token + user', () => {
    expectTypeOf<LoginResult>().toMatchTypeOf<{ id_token: string; user: CurrentUser }>()
  })
})
