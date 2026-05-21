import { describe, expect, it } from 'vitest'
import { downsampleIndices } from '@/lib/downsample'

describe('downsampleIndices', () => {
  it('returns all indices when n <= cap', () => {
    const idx = downsampleIndices(10, [], 5000)
    expect(idx.length).toBe(10)
    expect(idx[0]).toBe(0)
    expect(idx[9]).toBe(9)
  })

  it('caps at "cap" but unions must-include indices', () => {
    const flakeIds = Array.from({ length: 10000 }, (_, i) => i + 1)
    const mustInclude = new Set([5, 9999])
    const idx = downsampleIndices(10000, flakeIds, 5000, mustInclude)
    expect(idx.length).toBeLessThanOrEqual(5000)
    expect(idx.includes(4)).toBe(true)
    expect(idx.includes(9998)).toBe(true)
  })

  it('is deterministic given the same seed', () => {
    const a = downsampleIndices(10000, [], 100)
    const b = downsampleIndices(10000, [], 100)
    expect(a).toEqual(b)
  })
})
