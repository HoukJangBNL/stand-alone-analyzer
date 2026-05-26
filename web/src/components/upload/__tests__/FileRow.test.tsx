import { describe, it, expect, beforeEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { FileRow } from '../FileRow'
import { resetUploadStore, useUploadStore, type UploadFile } from '@/state/uploadSlice'

function mkFailedFile(uid: string, error: string, requestId: string | null): UploadFile {
  return {
    uid,
    file: new File([new Uint8Array(1)], `${uid}.tif`),
    filename: `${uid}.tif`,
    size: 1,
    grid_ix: 0,
    grid_iy: 0,
    status: 'failed',
    progress: 0,
    sha256_hex: null,
    upload_item_id: null,
    image_id: null,
    error,
    request_id: requestId,
  }
}

beforeEach(() => {
  resetUploadStore()
})

describe('FileRow', () => {
  it('renders the request_id alongside the error when both are present', () => {
    const uid = 'rowA'
    act(() => {
      useUploadStore.setState({
        files: { [uid]: mkFailedFile(uid, 'boom', 'req-abc') },
        order: [uid],
        scanId: null,
      })
    })

    render(<FileRow uid={uid} />)

    const reqIdEl = screen.getByTestId(`file-row-reqid-${uid}`)
    expect(reqIdEl.textContent).toContain('req-abc')
  })

  it('hides request_id when there is no error', () => {
    const uid = 'rowB'
    act(() => {
      useUploadStore.setState({
        files: {
          [uid]: {
            ...mkFailedFile(uid, '', null),
            status: 'queued',
            error: null,
            request_id: null,
          },
        },
        order: [uid],
        scanId: null,
      })
    })

    render(<FileRow uid={uid} />)

    expect(screen.queryByTestId(`file-row-reqid-${uid}`)).toBeNull()
  })

  it('hides request_id when the error is set but request_id is null', () => {
    const uid = 'rowC'
    act(() => {
      useUploadStore.setState({
        files: { [uid]: mkFailedFile(uid, 'boom', null) },
        order: [uid],
        scanId: null,
      })
    })

    render(<FileRow uid={uid} />)

    expect(screen.queryByTestId(`file-row-reqid-${uid}`)).toBeNull()
  })
})
