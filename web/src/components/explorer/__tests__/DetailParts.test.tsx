// web/src/components/explorer/__tests__/DetailParts.test.tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { DetailIdentity } from '../DetailIdentity'
import { DetailLabels } from '../DetailLabels'
import { DetailDistance } from '../DetailDistance'

describe('DetailIdentity', () => {
  it('shows the flake_id, stem, and pass/fail chip', () => {
    render(<DetailIdentity flakeId="A:3" stem="A" passes={true} />)
    expect(screen.getByText('A:3')).not.toBeNull()
    expect(screen.getByText('A')).not.toBeNull()
    expect(screen.getByText(/pass/i)).not.toBeNull()
  })

  it('renders FAIL chip when passes is false', () => {
    render(<DetailIdentity flakeId="B:1" stem="B" passes={false} />)
    expect(screen.getByText(/fail/i)).not.toBeNull()
  })
})

describe('DetailLabels', () => {
  it('renders one chip per cluster label using CLUSTER_PALETTE', () => {
    render(<DetailLabels labels={[{ label: 1, name: 'mono' }, { label: 2, name: 'bi' }]} />)
    expect(screen.getByText('mono')).not.toBeNull()
    expect(screen.getByText('bi')).not.toBeNull()
  })

  it('renders an em-dash when labels are empty', () => {
    render(<DetailLabels labels={[]} />)
    expect(screen.getByText('—')).not.toBeNull()
  })
})

describe('DetailDistance', () => {
  it('renders nearest-neighbor distance in micrometers with 2 decimals', () => {
    render(<DetailDistance distanceUm={3.14159} />)
    expect(screen.getByText(/3\.14/)).not.toBeNull()
    expect(screen.getByText(/µm/i)).not.toBeNull()
  })

  it('renders an em-dash when the distance is null', () => {
    render(<DetailDistance distanceUm={null} />)
    expect(screen.getByText('—')).not.toBeNull()
  })
})
