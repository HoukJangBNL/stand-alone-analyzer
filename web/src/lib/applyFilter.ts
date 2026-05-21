// web/src/lib/applyFilter.ts
import type { DomainStats } from '@/api/selector'
import type { FilterRanges } from '@/state/selectorSlice'

/**
 * Returns the set of flake_ids that pass all 5 metric ranges.
 * sam2 is treated as "no constraint" when the column is absent (matches
 * pipeline/selector.py:135-149 allow_missing semantics).
 */
export function computeAccepted(
  stats: DomainStats,
  filter: FilterRanges
): Set<number> {
  const out = new Set<number>()
  const n = stats.flake_ids.length
  const [aLo, aHi] = filter.area
  const [srLo, srHi] = filter.std_r
  const [sgLo, sgHi] = filter.std_g
  const [sbLo, sbHi] = filter.std_b
  const [s2Lo, s2Hi] = filter.sam2
  const sam2 = stats.sam2

  for (let i = 0; i < n; i++) {
    const a = stats.areas[i]
    if (a < aLo || a > aHi) continue
    if (stats.std_r[i] < srLo || stats.std_r[i] > srHi) continue
    if (stats.std_g[i] < sgLo || stats.std_g[i] > sgHi) continue
    if (stats.std_b[i] < sbLo || stats.std_b[i] > sbHi) continue
    if (sam2 !== undefined) {
      if (sam2[i] < s2Lo || sam2[i] > s2Hi) continue
    }
    out.add(stats.flake_ids[i])
  }
  return out
}
