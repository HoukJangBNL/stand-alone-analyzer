import { describe, it, expect } from 'vitest'
import { detectGrid } from '@/state/uploadSlice'

describe('detectGrid', () => {
  it('parses ix###_iy### scanner default', () => {
    expect(detectGrid('ix002_iy025.png')).toEqual({ ix: 2, iy: 25 })
    expect(detectGrid('IX10_IY7.tif')).toEqual({ ix: 10, iy: 7 })
  })

  it('parses tile_<ix>_<iy>', () => {
    expect(detectGrid('tile_3_5.tif')).toEqual({ ix: 3, iy: 5 })
    expect(detectGrid('Tile-12-9.JPG')).toEqual({ ix: 12, iy: 9 })
  })

  it('handles ix/iy embedded with prefix or suffix', () => {
    expect(detectGrid('scan_ix12_iy7_extra.png')).toEqual({ ix: 12, iy: 7 })
  })

  it('returns nulls when no pattern matches', () => {
    expect(detectGrid('random.png')).toEqual({ ix: null, iy: null })
    expect(detectGrid('image-001.jpg')).toEqual({ ix: null, iy: null })
  })
})
