import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { FileDropzone } from '../FileDropzone'
import { useUploadStore, resetUploadStore } from '@/state/uploadSlice'

beforeEach(() => {
  resetUploadStore()
})

// --- Mock helpers for the WebKit FileSystem API ---------------------------
type MockEntry = {
  isFile: boolean
  isDirectory: boolean
  name: string
  file?: (cb: (f: File) => void) => void
  createReader?: () => { readEntries: (cb: (e: MockEntry[]) => void) => void }
}

function makeFileEntry(file: File): MockEntry {
  return {
    isFile: true,
    isDirectory: false,
    name: file.name,
    file: (cb) => cb(file),
  }
}

function makeDirEntry(name: string, children: MockEntry[]): MockEntry {
  // readEntries spec: returns entries in batches; empty array signals done.
  let served = false
  return {
    isFile: false,
    isDirectory: true,
    name,
    createReader: () => ({
      readEntries: (cb) => {
        if (served) return cb([])
        served = true
        cb(children)
      },
    }),
  }
}

function makeItem(entry: MockEntry): DataTransferItem {
  return {
    kind: 'file',
    webkitGetAsEntry: () => entry,
  } as unknown as DataTransferItem
}

const flush = () => new Promise((r) => setTimeout(r, 0))

// --- Tests ----------------------------------------------------------------
describe('FileDropzone', () => {
  it('adds a single dropped image file via items API', async () => {
    render(<FileDropzone />)
    const dz = screen.getByTestId('file-dropzone')
    const f = new File([new Uint8Array(4)], 'tile_3_5.tif')
    fireEvent.drop(dz, {
      dataTransfer: { files: [], items: [makeItem(makeFileEntry(f))], types: ['Files'] },
    })
    await flush()
    const state = useUploadStore.getState()
    expect(state.order.length).toBe(1)
    expect(state.files[state.order[0]].grid_ix).toBe(3)
  })

  it('drops a folder of 3 images + 1 .txt → keeps only the 3 images', async () => {
    render(<FileDropzone />)
    const dz = screen.getByTestId('file-dropzone')
    const a = new File([new Uint8Array(1)], 'a.png')
    const b = new File([new Uint8Array(1)], 'b.jpg')
    const c = new File([new Uint8Array(1)], 'c.tif')
    const notes = new File([new Uint8Array(1)], 'notes.txt')
    const dir = makeDirEntry('scan', [
      makeFileEntry(a),
      makeFileEntry(b),
      makeFileEntry(c),
      makeFileEntry(notes),
    ])
    fireEvent.drop(dz, {
      dataTransfer: { files: [], items: [makeItem(dir)], types: ['Files'] },
    })
    await flush()
    const state = useUploadStore.getState()
    const names = state.order.map((uid) => state.files[uid].filename).sort()
    expect(names).toEqual(['a.png', 'b.jpg', 'c.tif'])
  })

  it('recurses into nested subfolders and aggregates all images', async () => {
    render(<FileDropzone />)
    const dz = screen.getByTestId('file-dropzone')
    const top = ['t1.png', 't2.jpg', 't3.tif'].map(
      (n) => new File([new Uint8Array(1)], n),
    )
    const nested = ['n1.png', 'n2.bmp'].map((n) => new File([new Uint8Array(1)], n))
    const sub = makeDirEntry('raw', nested.map(makeFileEntry))
    const dir = makeDirEntry('scan', [...top.map(makeFileEntry), sub])
    fireEvent.drop(dz, {
      dataTransfer: { files: [], items: [makeItem(dir)], types: ['Files'] },
    })
    await flush()
    await flush()
    const state = useUploadStore.getState()
    const names = state.order.map((uid) => state.files[uid].filename).sort()
    expect(names).toEqual(['n1.png', 'n2.bmp', 't1.png', 't2.jpg', 't3.tif'].sort())
  })

  it('picker change event filters by extension (case-insensitive)', () => {
    render(<FileDropzone />)
    const input = screen.getByTestId('file-dropzone-input') as HTMLInputElement
    const a = new File([new Uint8Array(1)], 'a.png')
    const txt = new File([new Uint8Array(1)], 'notes.txt')
    const b = new File([new Uint8Array(1)], 'b.JPG')
    Object.defineProperty(input, 'files', { value: [a, txt, b], configurable: true })
    fireEvent.change(input)
    const state = useUploadStore.getState()
    const names = state.order.map((uid) => state.files[uid].filename).sort()
    expect(names).toEqual(['a.png', 'b.JPG'].sort())
  })
})
