export function logToValue(t: number, min: number, max: number): number {
  const tt = Math.max(0, Math.min(1, t))
  return min * Math.pow(max / min, tt)
}

export function valueToLog(v: number, min: number, max: number): number {
  const vv = Math.max(min, Math.min(max, v))
  return Math.log(vv / min) / Math.log(max / min)
}
