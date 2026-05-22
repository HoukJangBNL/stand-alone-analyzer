import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MaterialCombobox } from '../MaterialCombobox'
import * as materialsApi from '@/api/materials'

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('MaterialCombobox', () => {
  it('shows fetched materials in dropdown and selects one', async () => {
    vi.spyOn(materialsApi, 'fetchMaterials').mockResolvedValue([
      { name: 'graphene' },
      { name: 'MoS2' },
    ])
    const onChange = vi.fn()
    render(wrap(<MaterialCombobox value="" onChange={onChange} />))
    await waitFor(() => expect(screen.getByTestId('material-combobox-input')).toBeTruthy())
    await userEvent.click(screen.getByTestId('material-combobox-input'))
    await waitFor(() => expect(screen.getByTestId('material-combobox-option-graphene')).toBeTruthy())
    await userEvent.click(screen.getByTestId('material-combobox-option-graphene'))
    expect(onChange).toHaveBeenCalledWith('graphene')
  })

  it('creates a new material via POST when user types unknown name + commits', async () => {
    vi.spyOn(materialsApi, 'fetchMaterials').mockResolvedValue([{ name: 'graphene' }])
    const createSpy = vi
      .spyOn(materialsApi, 'createMaterial')
      .mockResolvedValue({ name: 'NbSe2', created: true })
    const onChange = vi.fn()
    render(wrap(<MaterialCombobox value="" onChange={onChange} />))
    const input = await screen.findByTestId('material-combobox-input')
    await userEvent.type(input, 'NbSe2')
    await userEvent.click(screen.getByTestId('material-combobox-create-btn'))
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith('NbSe2'))
    expect(onChange).toHaveBeenCalledWith('NbSe2')
  })
})
