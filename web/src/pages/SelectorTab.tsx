// web/src/pages/SelectorTab.tsx
import { useDomainStats } from '@/hooks/useDomainStats'
import { SelectorMain } from '@/components/selector/SelectorMain'
import { SelectorRightRail } from '@/components/selector/SelectorRightRail'
import { FlakeListAccordion } from '@/components/selector/FlakeListAccordion'
import { CommitButton } from '@/components/selector/CommitButton'

interface SelectorTabProps {
  projectId: string
  scanId?: number | null
}

export function SelectorTab({ projectId, scanId = null }: SelectorTabProps) {
  if (!scanId) return <p data-testid="selector-tab-no-scan">Select a scan.</p>
  return <SelectorTabBody projectId={projectId} scanId={scanId} />
}

function SelectorTabBody({ projectId }: { projectId: string; scanId: number }) {
  const { data, isLoading, error } = useDomainStats(projectId)

  if (isLoading) {
    return <div style={{ padding: 16 }}>Loading domain stats...</div>
  }
  if (error) {
    return (
      <div role="alert" style={{ padding: 16, color: '#b91c1c' }}>
        {(error as Error).message}
      </div>
    )
  }
  if (!data) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, height: '100%' }}>
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <SelectorMain projectId={projectId} stats={data} />
        <SelectorRightRail projectId={projectId} stats={data} />
      </div>
      <FlakeListAccordion stats={data} />
      <div style={{ padding: 12, borderTop: '1px solid #eee' }}>
        {/* Body-level mirror of the right-rail commit per design §4.2 */}
        <CommitButton projectId={projectId} />
      </div>
    </div>
  )
}
