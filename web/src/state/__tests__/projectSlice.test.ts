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

describe('projectSlice — activeScanId (W10-D)', () => {
  beforeEach(() => {
    resetProjectStore()
  })

  it('starts as null', () => {
    expect(useProjectStore.getState().activeScanId).toBeNull()
  })

  it('setActiveScanId stores the value', () => {
    useProjectStore.getState().setActiveScanId(42)
    expect(useProjectStore.getState().activeScanId).toBe(42)
  })

  it('setActiveScanId(null) clears it', () => {
    useProjectStore.getState().setActiveScanId(7)
    useProjectStore.getState().setActiveScanId(null)
    expect(useProjectStore.getState().activeScanId).toBeNull()
  })

  it('resetProjectStore clears both fields', () => {
    useProjectStore.getState().setActiveProjectId('p1')
    useProjectStore.getState().setActiveScanId(11)
    resetProjectStore()
    expect(useProjectStore.getState().activeProjectId).toBeNull()
    expect(useProjectStore.getState().activeScanId).toBeNull()
  })

  it('changing project clears the scan (different project ⇒ different scans)', () => {
    useProjectStore.getState().setActiveProjectId('p1')
    useProjectStore.getState().setActiveScanId(11)
    useProjectStore.getState().setActiveProjectId('p2')
    expect(useProjectStore.getState().activeScanId).toBeNull()
  })
})
