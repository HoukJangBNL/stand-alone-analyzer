import { describe, it, expect, beforeEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ProgressList } from '../ProgressList'
import { resetUploadStore, useUploadStore, type UploadFile } from '@/state/uploadSlice'

function mkFile(uid: string, status: UploadFile['status']): UploadFile {
  return {
    uid,
    file: new File([new Uint8Array(1)], `${uid}.tif`),
    filename: `${uid}.tif`,
    size: 1,
    grid_ix: null,
    grid_iy: null,
    status,
    progress: 0,
    sha256_hex: null,
    upload_item_id: null,
    image_id: null,
    error: null,
  }
}

function populate(total: number, failedFromIndex: number) {
  const files: Record<string, UploadFile> = {}
  const order: string[] = []
  for (let i = 0; i < total; i++) {
    const uid = `f${i}`
    const status: UploadFile['status'] = i >= failedFromIndex ? 'failed' : 'queued'
    files[uid] = mkFile(uid, status)
    order.push(uid)
  }
  act(() => {
    useUploadStore.setState({ files, order, scanId: null })
  })
}

beforeEach(() => {
  resetUploadStore()
})

describe('ProgressList', () => {
  it('caps visible rows at 200 and shows truncation by default; "Failed only" filters before cap', async () => {
    // 250 files: 0..199 queued, 200..249 failed (50 failed total).
    populate(250, 200)

    render(<ProgressList />)

    // Default view: at most 200 rows visible, truncation message shown.
    const allRowsDefault = screen.getAllByTestId(/^file-row-f\d+$/)
    expect(allRowsDefault.length).toBeLessThanOrEqual(200)
    expect(allRowsDefault.length).toBe(200)
    expect(screen.getByTestId('progress-list-truncated')).toBeTruthy()

    // Toggle "Failed only".
    const toggle = screen.getByTestId('progress-list-failed-only')
    await userEvent.click(toggle)

    // After toggle: only 50 failed rows visible, no truncation message.
    const allRowsFailed = screen.getAllByTestId(/^file-row-f\d+$/)
    expect(allRowsFailed.length).toBe(50)
    // Verify each visible row is failed.
    for (const row of allRowsFailed) {
      const uid = row.getAttribute('data-testid')!.replace('file-row-', '')
      expect(useUploadStore.getState().files[uid].status).toBe('failed')
    }
    expect(screen.queryByTestId('progress-list-truncated')).toBeNull()
  })
})
