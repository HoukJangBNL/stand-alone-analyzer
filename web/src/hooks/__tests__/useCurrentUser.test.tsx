import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render } from '@testing-library/react'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { useAuthStore, resetAuthStore } from '@/state/authSlice'

function TestComponent() {
  useCurrentUser()
  return <div>Test</div>
}

describe('useCurrentUser', () => {
  beforeEach(() => {
    resetAuthStore()
    vi.clearAllMocks()
  })

  it('calls hydrate on mount', () => {
    const spy = vi.spyOn(useAuthStore.getState(), 'hydrate')
    render(<TestComponent />)
    expect(spy).toHaveBeenCalledOnce()
  })
})
