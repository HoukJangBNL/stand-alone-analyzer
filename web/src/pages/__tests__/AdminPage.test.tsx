import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AdminPage } from '@/pages/AdminPage'
import * as adminApi from '@/api/admin'

vi.mock('@/api/admin')

describe('<AdminPage>', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders usage rows', async () => {
    vi.mocked(adminApi.fetchUsage).mockResolvedValue([
      { id: '1', user_id: 'u1', kind: 'login', ts: '2026-05-21T10:00:00Z' },
      { id: '2', user_id: 'u2', kind: 'logout', ts: '2026-05-21T11:00:00Z' },
    ])
    render(<AdminPage />)
    await waitFor(() => {
      const rows = screen.getAllByTestId(/^admin-usage-row/)
      expect(rows).toHaveLength(2)
    })
  })

  it('role-select submits', async () => {
    vi.mocked(adminApi.fetchUsage).mockResolvedValue([])
    vi.mocked(adminApi.updateUserRole).mockResolvedValue(undefined)
    render(<AdminPage />)
    const input = screen.getByTestId('admin-user-id-input')
    const select = screen.getByTestId('admin-role-select')
    const submitBtn = screen.getByTestId('admin-role-submit')

    fireEvent.change(input, { target: { value: 'u1' } })
    fireEvent.change(select, { target: { value: 'operator' } })
    fireEvent.click(submitBtn)

    await waitFor(() => expect(adminApi.updateUserRole).toHaveBeenCalledWith('u1', 'operator'))
  })
})
