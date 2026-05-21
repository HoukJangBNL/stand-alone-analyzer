// web/src/components/selector/FlakeListAccordion.tsx
import { useState } from 'react'
import type { DomainStats } from '@/api/selector'
import { FlakeTable } from './FlakeTable'

interface Props {
  stats: DomainStats
}

export function FlakeListAccordion({ stats }: Props) {
  const [open, setOpen] = useState(false)
  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)} style={{ marginTop: 12 }}>
      <summary style={{ cursor: 'pointer', fontWeight: 600 }}>Flake list ({stats.flake_ids.length})</summary>
      <div style={{ marginTop: 8 }}>
        {open && <FlakeTable stats={stats} />}
      </div>
    </details>
  )
}
