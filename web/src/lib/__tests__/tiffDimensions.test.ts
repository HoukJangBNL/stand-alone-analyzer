import { describe, it, expect } from 'vitest'
import { parseTiffDimensions, isTiffFilename, readTiffDimensions } from '@/lib/tiffDimensions'

/**
 * Build a minimal valid TIFF header buffer with two IFD0 entries:
 * ImageWidth (0x0100) and ImageLength (0x0101), both as LONG (u32).
 */
function makeTiffHeader(width: number, height: number, little = true): ArrayBuffer {
  const buf = new ArrayBuffer(8 + 2 + 12 * 2 + 4)
  const view = new DataView(buf)
  if (little) {
    view.setUint8(0, 0x49)
    view.setUint8(1, 0x49)
  } else {
    view.setUint8(0, 0x4d)
    view.setUint8(1, 0x4d)
  }
  view.setUint16(2, 42, little)
  view.setUint32(4, 8, little) // IFD0 starts at byte 8
  view.setUint16(8, 2, little) // 2 entries

  // Entry 1: ImageWidth, LONG, count=1, value=width
  view.setUint16(10, 0x0100, little)
  view.setUint16(12, 4, little)
  view.setUint32(14, 1, little)
  view.setUint32(18, width, little)

  // Entry 2: ImageLength, LONG, count=1, value=height
  view.setUint16(22, 0x0101, little)
  view.setUint16(24, 4, little)
  view.setUint32(26, 1, little)
  view.setUint32(30, height, little)

  // Next IFD offset (none)
  view.setUint32(34, 0, little)
  return buf
}

describe('parseTiffDimensions', () => {
  it('reads width/height from a little-endian TIFF', () => {
    const buf = makeTiffHeader(2048, 1536, true)
    expect(parseTiffDimensions(buf)).toEqual({ width: 2048, height: 1536 })
  })

  it('reads width/height from a big-endian TIFF', () => {
    const buf = makeTiffHeader(640, 480, false)
    expect(parseTiffDimensions(buf)).toEqual({ width: 640, height: 480 })
  })

  it('throws on bad byte-order marker', () => {
    const buf = new ArrayBuffer(16)
    const view = new DataView(buf)
    view.setUint8(0, 0x00)
    view.setUint8(1, 0x00)
    expect(() => parseTiffDimensions(buf)).toThrow(/byte order/)
  })

  it('throws on bad magic', () => {
    const buf = makeTiffHeader(10, 10, true)
    new DataView(buf).setUint16(2, 99, true)
    expect(() => parseTiffDimensions(buf)).toThrow(/magic/)
  })
})

describe('isTiffFilename', () => {
  it('matches .tif and .tiff case-insensitively', () => {
    expect(isTiffFilename('a.tif')).toBe(true)
    expect(isTiffFilename('a.tiff')).toBe(true)
    expect(isTiffFilename('a.TIFF')).toBe(true)
    expect(isTiffFilename('a.png')).toBe(false)
    expect(isTiffFilename('atif')).toBe(false)
  })
})

describe('readTiffDimensions', () => {
  it('round-trips through a File', async () => {
    const buf = makeTiffHeader(800, 600, true)
    const file = new File([buf], 'wafer.tif')
    expect(await readTiffDimensions(file)).toEqual({ width: 800, height: 600 })
  })
})
