// Ports CLUSTER_PALETTE + NEUTRAL_GRAY from src/flake_analysis/ui/tab_clustering.py:35-39.

export const CLUSTER_PALETTE = [
  '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
] as const

export const NEUTRAL_GRAY = '#9e9e9e'

export function colorForLabel(label: number): string {
  if (label < 0) return NEUTRAL_GRAY
  return CLUSTER_PALETTE[label % CLUSTER_PALETTE.length]
}

export const colorForCluster = colorForLabel
