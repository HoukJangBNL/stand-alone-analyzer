import { describe, it, expect } from 'vitest'
import { ScanPicker } from '@/components/scans/ScanPicker'
import { ScanTable } from '@/components/scans/ScanTable'

describe('ScanPicker (compat shim)', () => {
  it('re-exports ScanTable under the legacy name', () => {
    expect(ScanPicker).toBe(ScanTable)
  })
})
