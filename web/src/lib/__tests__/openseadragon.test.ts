// web/src/lib/__tests__/openseadragon.test.ts
import { describe, it, expect, vi } from 'vitest'

vi.mock('openseadragon', () => ({
  default: vi.fn(() => ({ destroy: vi.fn() })),
}))

import OSD from '../openseadragon'

describe('lib/openseadragon', () => {
  it('re-exports the default export of the openseadragon package', () => {
    expect(typeof OSD).toBe('function')
    const v = OSD({ id: 'x', tileSources: [] } as unknown as Parameters<typeof OSD>[0])
    expect(v).toBeDefined()
    expect(typeof (v as { destroy: () => void }).destroy).toBe('function')
  })
})
