import { describe, expect, it } from 'vitest'
import { pickFocusDomainId } from '@/lib/focus'
import { emptyBrushing, applyLasso, setFocusId } from '@/lib/brushing'

describe('pickFocusDomainId — ports tab_selector.py:695-708', () => {
  it('returns explicit focus_id when set (priority 1)', () => {
    let s = emptyBrushing()
    s = applyLasso(s, [3, 4, 5], 'replace')
    s = setFocusId(s, 99)
    expect(pickFocusDomainId(s)).toBe(99)
  })

  it('returns min(selectedIds) when no explicit focus (priority 2)', () => {
    let s = emptyBrushing()
    s = applyLasso(s, [3, 4, 5], 'replace')
    expect(pickFocusDomainId(s)).toBe(3)
  })

  it('returns null when neither focus nor selection (priority 3)', () => {
    expect(pickFocusDomainId(emptyBrushing())).toBeNull()
  })
})
