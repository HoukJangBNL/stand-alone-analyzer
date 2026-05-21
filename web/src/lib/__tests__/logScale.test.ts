import { describe, it, expect } from 'vitest'
import { logToValue, valueToLog } from '@/lib/logScale'

const MIN = 0.1
const MAX = 10.0

describe('logScale [0.1, 10.0]', () => {
  it('maps slider 0 to MIN and slider 1 to MAX', () => {
    expect(logToValue(0, MIN, MAX)).toBeCloseTo(MIN, 6)
    expect(logToValue(1, MIN, MAX)).toBeCloseTo(MAX, 6)
  })

  it('round-trips candidates within float tolerance', () => {
    for (const v of [0.1, 0.3, 1.0, 3.0, 10.0]) {
      const t = valueToLog(v, MIN, MAX)
      const back = logToValue(t, MIN, MAX)
      expect(back).toBeCloseTo(v, 6)
    }
  })

  it('clamps inputs outside the band', () => {
    expect(valueToLog(0.05, MIN, MAX)).toBe(0)
    expect(valueToLog(50, MIN, MAX)).toBe(1)
  })
})
