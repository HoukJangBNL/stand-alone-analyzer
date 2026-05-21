// web/src/components/explorer/__tests__/DetailParts.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DetailIdentity } from '../DetailIdentity'
import { DetailLabels } from '../DetailLabels'
import { DetailDistance } from '../DetailDistance'

describe('DetailIdentity', () => {
  it('shows the flake_id and image_id (no chip when passes is omitted)', () => {
    render(<DetailIdentity flakeId={3} imageId={42} />)
    expect(screen.getByText('3')).not.toBeNull()
    expect(screen.getByText('42')).not.toBeNull()
    expect(screen.queryByTestId('pass-chip')).toBeNull()
  })

  it('renders PASS chip when passes is true', () => {
    render(<DetailIdentity flakeId={3} imageId={42} passes={true} />)
    expect(screen.getByText(/pass/i)).not.toBeNull()
  })

  it('renders FAIL chip when passes is false', () => {
    render(<DetailIdentity flakeId={1} imageId={2} passes={false} />)
    expect(screen.getByText(/fail/i)).not.toBeNull()
  })
})

describe('DetailLabels', () => {
  it('renders one chip per cluster name using CLUSTER_PALETTE by index', () => {
    render(<DetailLabels names={['mono', 'bi']} />)
    expect(screen.getByText('mono')).not.toBeNull()
    expect(screen.getByText('bi')).not.toBeNull()
  })

  it('renders an em-dash when names are empty', () => {
    render(<DetailLabels names={[]} />)
    expect(screen.getByText('—')).not.toBeNull()
  })
})

describe('DetailDistance', () => {
  it('renders nearest-neighbor distance in pixels with 2 decimals', () => {
    render(<DetailDistance distancePx={3.14159} />)
    expect(screen.getByText(/3\.14/)).not.toBeNull()
    expect(screen.getByText(/px/i)).not.toBeNull()
  })

  it('renders an em-dash when the distance is null', () => {
    render(<DetailDistance distancePx={null} />)
    expect(screen.getByText('—')).not.toBeNull()
  })
})
