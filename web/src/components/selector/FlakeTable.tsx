// web/src/components/selector/FlakeTable.tsx
import { useMemo } from 'react'
import { FixedSizeList as List } from 'react-window'
import type { DomainStats } from '@/api/selector'
import { useSelectorStore } from '@/state/selectorSlice'
import { computeAccepted } from '@/lib/applyFilter'

interface FlakeTableProps {
  stats: DomainStats
}

const ROW_HEIGHT = 24

export function FlakeTable({ stats }: FlakeTableProps) {
  const filter = useSelectorStore((s) => s.filter)
  const setFocusId = useSelectorStore((s) => s.setFocusId)
  const selectedIds = useSelectorStore((s) => s.brushing.selectedIds)

  const rows = useMemo(() => {
    const accepted = computeAccepted(stats, filter)
    const out: Array<{ id: number; area: number; std_r: number; selected: boolean }> = []
    for (let i = 0; i < stats.flake_ids.length; i++) {
      const id = stats.flake_ids[i]
      if (!accepted.has(id)) continue
      out.push({
        id,
        area: stats.areas[i],
        std_r: stats.std_r[i],
        selected: selectedIds.has(id),
      })
    }
    return out
  }, [stats, filter, selectedIds])

  return (
    <div role="table">
      <div role="row" style={{ display: 'grid', gridTemplateColumns: '60px 80px 80px 60px', fontWeight: 600, padding: '4px 8px', borderBottom: '1px solid #ddd' }}>
        <span>id</span><span>area</span><span>std_r</span><span>sel</span>
      </div>
      <List
        height={Math.min(360, ROW_HEIGHT * rows.length || ROW_HEIGHT)}
        itemCount={rows.length}
        itemSize={ROW_HEIGHT}
        width="100%"
      >
        {({ index, style }) => {
          const r = rows[index]
          return (
            <div
              role="row"
              data-testid={`flake-row-${r.id}`}
              key={r.id}
              style={{ ...style, display: 'grid', gridTemplateColumns: '60px 80px 80px 60px', padding: '4px 8px', cursor: 'pointer', background: r.selected ? '#fee2e2' : 'transparent' }}
              onClick={() => setFocusId(r.id)}
            >
              <span>{r.id}</span>
              <span>{r.area}</span>
              <span>{r.std_r.toFixed(2)}</span>
              <span>{r.selected ? 'Y' : ''}</span>
            </div>
          )
        }}
      </List>
    </div>
  )
}
