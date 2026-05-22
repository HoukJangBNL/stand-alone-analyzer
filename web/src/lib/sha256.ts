// web/src/lib/sha256.ts
/**
 * Compute lowercase hex SHA256 of a File using Web Crypto.
 * Backend (W5-B) re-derives the same digest server-side and converts to base64
 * for the `x-amz-checksum-sha256` PUT header — UI only deals in hex.
 */
export async function sha256Hex(file: File): Promise<string> {
  const buffer = await file.arrayBuffer()
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  const bytes = new Uint8Array(digest)
  let out = ''
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, '0')
  }
  return out
}
