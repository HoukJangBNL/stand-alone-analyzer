import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FileDropzone } from '../FileDropzone'
import { useUploadStore, resetUploadStore } from '@/state/uploadSlice'

beforeEach(() => {
  resetUploadStore()
})

describe('FileDropzone', () => {
  it('adds dropped files to the upload store with auto-detected ix/iy', () => {
    render(<FileDropzone />)
    const dz = screen.getByTestId('file-dropzone')
    const f1 = new File([new Uint8Array(4)], 'tile_3_5.tif')
    const f2 = new File([new Uint8Array(4)], 'random.tif')
    fireEvent.drop(dz, {
      dataTransfer: { files: [f1, f2], items: [], types: ['Files'] },
    })
    const state = useUploadStore.getState()
    expect(state.order.length).toBe(2)
    const first = state.files[state.order[0]]
    const second = state.files[state.order[1]]
    expect(first.grid_ix).toBe(3)
    expect(first.grid_iy).toBe(5)
    expect(second.grid_ix).toBeNull()
    expect(second.grid_iy).toBeNull()
  })

  it('also accepts files via the hidden input picker', () => {
    render(<FileDropzone />)
    const input = screen.getByTestId('file-dropzone-input') as HTMLInputElement
    const f = new File([new Uint8Array(4)], 'tile_0_0.tif')
    Object.defineProperty(input, 'files', { value: [f], configurable: true })
    fireEvent.change(input)
    expect(useUploadStore.getState().order.length).toBe(1)
  })
})
