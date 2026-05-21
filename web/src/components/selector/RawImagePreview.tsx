// web/src/components/selector/RawImagePreview.tsx
import { useState } from 'react'
import { usePanZoom } from '@/lib/usePanZoom'
import { useAnnotationPreview } from '@/hooks/useAnnotationPreview'

interface RawImagePreviewProps {
  projectId: string
  domainId: number | null
}

export function RawImagePreview({ projectId, domainId }: RawImagePreviewProps) {
  const [withContour, setWithContour] = useState(false)
  const url = useAnnotationPreview(projectId, domainId, withContour)
  const { wrapperProps, imgStyle, reset } = usePanZoom()

  if (!url) {
    return (
      <div style={{ padding: 16, color: '#888', fontStyle: 'italic' }}>
        Click a point or row to preview a domain.
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
        <label style={{ fontSize: 12 }}>
          <input
            type="checkbox"
            checked={withContour}
            onChange={(e) => setWithContour(e.target.checked)}
          />{' '}
          Show boundary
        </label>
        <button onClick={reset} aria-label="Reset zoom">Reset</button>
      </div>
      <div
        data-testid="panzoom-wrapper"
        {...wrapperProps}
        style={{
          width: '100%',
          height: 320,
          overflow: 'hidden',
          background: '#111',
          position: 'relative',
        }}
      >
        <img
          src={url}
          alt={`Domain ${domainId}`}
          style={imgStyle}
          draggable={false}
        />
      </div>
    </div>
  )
}
