// web/src/components/selector/ImagePreviewPanel.tsx
import { useSelectorStore } from '@/state/selectorSlice'
import { pickFocusDomainId } from '@/lib/focus'
import { RawImagePreview } from './RawImagePreview'

interface Props {
  projectId: string
}

export function ImagePreviewPanel({ projectId }: Props) {
  const focus = useSelectorStore((s) => pickFocusDomainId(s.brushing))
  return (
    <div style={{ width: 380 }}>
      <h4 style={{ margin: '0 0 6px 0' }}>Preview {focus !== null ? `(domain ${focus})` : ''}</h4>
      <RawImagePreview projectId={projectId} domainId={focus} />
    </div>
  )
}
