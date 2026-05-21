// web/src/state/__tests__/projectSlice.test.ts
import { describe, it, expect, beforeEach } from 'vitest'
import { useProjectStore, resetProjectStore } from '@/state/projectSlice'

describe('projectSlice', () => {
  beforeEach(() => resetProjectStore())

  it('starts with activeProjectId = null', () => {
    expect(useProjectStore.getState().activeProjectId).toBeNull()
  })

  it('setActiveProjectId stores the id', () => {
    useProjectStore.getState().setActiveProjectId('p1')
    expect(useProjectStore.getState().activeProjectId).toBe('p1')
  })

  it('setActiveProjectId(null) clears', () => {
    useProjectStore.getState().setActiveProjectId('p1')
    useProjectStore.getState().setActiveProjectId(null)
    expect(useProjectStore.getState().activeProjectId).toBeNull()
  })
})
