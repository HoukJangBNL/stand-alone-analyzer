// web/src/components/selector/__tests__/RawImagePreview.test.tsx
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { RawImagePreview } from '@/components/selector/RawImagePreview'

describe('RawImagePreview (Q-U3 — native <img>, NOT OpenSeadragon)', () => {
  it('renders nothing when domainId is null', () => {
    const { container } = render(<RawImagePreview projectId="local" domainId={null} />)
    expect(container.querySelector('img')).toBeNull()
  })

  it('renders an <img> with the preview URL when domainId is set', () => {
    render(<RawImagePreview projectId="local" domainId={7} />)
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.src).toContain('/api/v1/projects/local/data/annotations/7/preview')
    expect(img.src).toContain('with_contour=false')
  })

  it('toggles contour overlay via the boundary toggle', () => {
    render(<RawImagePreview projectId="local" domainId={7} />)
    const toggle = screen.getByRole('checkbox', { name: /Show boundary/ })
    fireEvent.click(toggle)
    const img = screen.getByRole('img') as HTMLImageElement
    expect(img.src).toContain('with_contour=true')
  })

  it('wheel event scales the image', () => {
    const { container } = render(<RawImagePreview projectId="local" domainId={7} />)
    const wrapper = container.querySelector('[data-testid="panzoom-wrapper"]') as HTMLElement
    fireEvent.wheel(wrapper, { deltaY: -100, ctrlKey: false })
    const img = container.querySelector('img') as HTMLImageElement
    // After wheel zoom in, transform style should include scale > 1
    expect(img.style.transform).toMatch(/scale\([12]\.\d/)
  })
})
