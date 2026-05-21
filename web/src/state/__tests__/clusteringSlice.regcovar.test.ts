import { describe, it, expect, beforeEach } from 'vitest'
import { useClusteringStore, resetClusteringStore } from '@/state/clusteringSlice'

describe('clusteringSlice.regCovar', () => {
  beforeEach(() => resetClusteringStore())

  it('defaults regCovar to 10.0', () => {
    expect(useClusteringStore.getState().regCovar).toBe(10.0)
  })

  it('setRegCovar updates the value', () => {
    useClusteringStore.getState().setRegCovar(3.0)
    expect(useClusteringStore.getState().regCovar).toBe(3.0)
  })

  it('reset returns regCovar to 10.0', () => {
    useClusteringStore.getState().setRegCovar(0.1)
    resetClusteringStore()
    expect(useClusteringStore.getState().regCovar).toBe(10.0)
  })
})
