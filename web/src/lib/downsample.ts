/**
 * Port of tab_selector.py:299-322 — keep at most ``cap`` indices but always
 * include any indices whose flake_id appears in ``mustIncludeIds``.
 *
 * Uses a fixed-seed mulberry32 PRNG so ScatterCanvas re-renders return the
 * same pinned subset across renders (no flicker on filter changes).
 */
export function downsampleIndices(
  n: number,
  flakeIds: number[],
  cap: number,
  mustIncludeIds?: Set<number>
): number[] {
  if (n <= cap) {
    const all = new Array<number>(n)
    for (let i = 0; i < n; i++) all[i] = i
    return all
  }
  const rng = mulberry32(0)
  const picked = new Set<number>()
  while (picked.size < cap) {
    picked.add(Math.floor(rng() * n))
  }
  if (mustIncludeIds && mustIncludeIds.size > 0 && flakeIds.length === n) {
    for (let i = 0; i < n; i++) {
      if (mustIncludeIds.has(flakeIds[i])) picked.add(i)
    }
  }
  return Array.from(picked).sort((a, b) => a - b).slice(0, Math.max(cap, mustIncludeIds?.size ?? 0))
}

function mulberry32(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6D2B79F5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}
