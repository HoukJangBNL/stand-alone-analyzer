import { describe, it, expect } from 'vitest'
import { sha256Hex } from '@/lib/sha256'

describe('sha256Hex', () => {
  it('hashes the empty string to the canonical SHA256 hex', async () => {
    const empty = new File([], 'empty.bin')
    const hex = await sha256Hex(empty)
    // SHA256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    expect(hex).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
  })

  it('hashes the literal string "abc" to the canonical SHA256 hex', async () => {
    const file = new File([new TextEncoder().encode('abc')], 'abc.txt')
    const hex = await sha256Hex(file)
    // SHA256("abc") = ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
    expect(hex).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  })

  it('returns 64-char lowercase hex for any input', async () => {
    const file = new File([new Uint8Array(1024)], 'zero.bin')
    const hex = await sha256Hex(file)
    expect(hex).toMatch(/^[0-9a-f]{64}$/)
  })
})
