import { describe, it, expect } from 'vitest'
import { CLUSTER_PALETTE, NEUTRAL_GRAY, colorForLabel } from '@/lib/clusterColors'

describe('clusterColors', () => {
  it('CLUSTER_PALETTE is the d3 category10 sequence (matches tab_clustering.py:35-39)', () => {
    expect(CLUSTER_PALETTE).toEqual([
      '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
      '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
    ])
  })

  it('NEUTRAL_GRAY matches the tab_clustering.py constant', () => {
    expect(NEUTRAL_GRAY).toBe('#9e9e9e')
  })

  it('colorForLabel returns palette color for label >= 0', () => {
    expect(colorForLabel(0)).toBe('#1f77b4')
    expect(colorForLabel(2)).toBe('#2ca02c')
  })

  it('colorForLabel wraps around past length 10', () => {
    expect(colorForLabel(10)).toBe('#1f77b4')
    expect(colorForLabel(15)).toBe('#8c564b')
  })

  it('colorForLabel returns NEUTRAL_GRAY for label === -1', () => {
    expect(colorForLabel(-1)).toBe('#9e9e9e')
  })

  it('colorForLabel returns NEUTRAL_GRAY for any negative label', () => {
    expect(colorForLabel(-2)).toBe('#9e9e9e')
  })
})
