// web/src/lib/tiffDimensions.ts
//
// Read width/height from a TIFF without decoding pixels. Browsers (Chrome,
// Safari, Firefox) cannot decode TIFF via createImageBitmap or <img>, but our
// upload `complete` step needs pixel dimensions. The values live in IFD0 tags
// ImageWidth (0x0100) and ImageLength (0x0101), which appear within the first
// few hundred bytes for any normal scan-style TIFF — far before image data.

const TAG_IMAGE_WIDTH = 0x0100
const TAG_IMAGE_LENGTH = 0x0101

const TYPE_SHORT = 3 // u16
const TYPE_LONG = 4 // u32

/** Throws if `buf` is not a parseable TIFF header. */
export function parseTiffDimensions(buf: ArrayBuffer): { width: number; height: number } {
  const view = new DataView(buf)
  if (view.byteLength < 8) throw new Error('tiff: header too short')

  const b0 = view.getUint8(0)
  const b1 = view.getUint8(1)
  let little: boolean
  if (b0 === 0x49 && b1 === 0x49) little = true // "II"
  else if (b0 === 0x4d && b1 === 0x4d) little = false // "MM"
  else throw new Error('tiff: bad byte order marker')

  const magic = view.getUint16(2, little)
  if (magic !== 42) throw new Error('tiff: bad magic')

  const ifd0Offset = view.getUint32(4, little)
  if (ifd0Offset + 2 > view.byteLength) throw new Error('tiff: ifd0 out of range')

  const entryCount = view.getUint16(ifd0Offset, little)
  let width: number | null = null
  let height: number | null = null
  for (let i = 0; i < entryCount; i++) {
    const off = ifd0Offset + 2 + i * 12
    if (off + 12 > view.byteLength) throw new Error('tiff: ifd entry out of range')
    const tag = view.getUint16(off, little)
    const type = view.getUint16(off + 2, little)
    // count is at off+4 (u32) — always 1 for these tags; skip reading.
    let value: number
    if (type === TYPE_SHORT) value = view.getUint16(off + 8, little)
    else if (type === TYPE_LONG) value = view.getUint32(off + 8, little)
    else continue
    if (tag === TAG_IMAGE_WIDTH) width = value
    else if (tag === TAG_IMAGE_LENGTH) height = value
    if (width !== null && height !== null) break
  }
  if (width === null || height === null) throw new Error('tiff: width/height tags missing')
  return { width, height }
}

/** True when the filename's extension says TIFF. */
export function isTiffFilename(name: string): boolean {
  return /\.tiff?$/i.test(name)
}

/** Read up to `byteCount` bytes from the start of the file. */
async function readPrefix(file: File, byteCount: number): Promise<ArrayBuffer> {
  const slice = file.slice(0, Math.min(byteCount, file.size))
  return await slice.arrayBuffer()
}

/**
 * Resolve TIFF dimensions by reading the header only. Reads 64KB which covers
 * IFD0 for any tile-style scan TIFF; if the IFD reference points further (rare
 * for our data), the caller should treat the throw as a fatal upload error.
 */
export async function readTiffDimensions(file: File): Promise<{ width: number; height: number }> {
  const buf = await readPrefix(file, 64 * 1024)
  return parseTiffDimensions(buf)
}
