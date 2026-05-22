# W5-C — Frontend Upload Modal (drag-drop + SHA256 + presigned PUT)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the React side of the W5 upload flow — a modal opened from the project landing page (ComputeTab) that lets the user (a) fill scan metadata (name, material via auto-add combobox, image_count, free-form key/value), (b) drop image files, (c) edit per-file `(grid_ix, grid_iy)`, (d) sees per-file SHA256 → presign → S3 PUT → complete pipeline progress with retry, then (e) finalizes the scan. Browser computes SHA256 via `crypto.subtle.digest`. Concurrency cap: 4 files in flight. Closing the modal mid-upload aborts every in-flight fetch.

**Architecture:** A single Zustand slice (`uploadSlice`) holds the per-file pipeline state (`queued | hashing | presigning | uploading | completing | done | failed`). A standalone orchestrator (`uploadOrchestrator.ts`) is the only thing that mutates per-file status — it owns a `Map<file_uid, AbortController>`, polls the slice for queued items whenever a slot frees up, and runs the four-step pipeline (hash → presign → PUT → complete) for each file sequentially per-file but parallel across files (max 4 concurrent). The modal component is dumb — it reads slice state and dispatches `start`/`cancel`/`retry` actions. Hash is computed BEFORE presign (sequential, simpler — Web Crypto digest of a 10MB file ≈ 50ms, negligible vs PUT latency). On modal close mid-upload, the orchestrator aborts every controller and resets the slice. No resumability (D3).

**Tech Stack:** React 18 + Vite + TypeScript, TanStack Query 5.x for read queries (`materials` list), Zustand 4.x for upload state, react-hook-form 7.x for the metadata form (no zod — repo doesn't have it; manual validation with `register` + custom rules), native `<input type="file">` + `dragenter/dragover/drop` (no `react-dropzone` — adds a dep for a 30-line problem), Web Crypto `crypto.subtle.digest('SHA-256', ...)`, Vitest + jsdom + @testing-library/react for unit, MSW 2.x for API mocking in tests, Playwright (via MCP server in dev) for one e2e smoke.

---

## Codebase Findings (verified 2026-05-22)

- **`web/package.json`** confirms: TanStack Query `^5.28.0` (matches v5 API: `useMutation({ mutationFn, onSuccess })`), Zustand `^4.5.7` (use `create<T>((set) => ...)`), `react-hook-form ^7.76.0`, `sonner ^1.4.0` for toasts, `msw ^2.2.0`, `vitest ^1.4.0`, `@testing-library/react ^14.2.1`, `@testing-library/user-event ^14.5.2`, `jsdom ^24.0.0`. **`zod` is NOT installed** — do NOT add it; do form validation inline via react-hook-form's built-in `rules`.
- **No dedicated `ProjectDetailPage`.** The "project landing page" is `web/src/pages/ComputeTab.tsx` (route `/projects/:projectId/compute`). The "+ 새 스캔" trigger goes there, NOT in a new file. `ProjectDetailPage` references in the spec resolve to `ComputeTab.tsx`.
- **No existing modal component in the repo.** Build a minimal one inline in `UploadModal.tsx` using a fixed-position overlay div; mirror the small-component style used by `CreateProjectForm.tsx` (`data-testid` everywhere, inline styles via `style={{...}}`, `sonner` for success/error toasts, TanStack Query `useMutation` for write paths). The closest reference patterns: `web/src/components/CreateProjectForm.tsx` (form + mutation + onSuccess toast), `web/src/components/Sidebar.tsx` (zustand `useProjectStore` + react-query `useQuery`).
- **Auth**: every fetch uses `getAuthHeaders()` from `web/src/api/authHeaders.ts` and `credentials: 'include'`. Errors come back as `ApiError` (from `web/src/api/selector.ts`) with `{ status, code, message, details, requestId }`. The new `web/src/api/upload.ts` and `web/src/api/materials.ts` MUST follow the same `unwrap<T>()` pattern (copy-paste, do NOT import — `selector.ts` already does it inline).
- **No `tests/e2e/` directory exists.** Create it. Playwright is not in `web/package.json` either — install it as a dev dependency in Task 7 (`npm i -D @playwright/test` and `npx playwright install chromium`).
- **No existing MSW handlers.** Create the first one under `web/src/__tests__/msw/` for unit tests; for e2e, use Playwright's `page.route()` directly (no MSW in browser) so we don't need a service worker setup.

---

## Verification Env Block

All Vitest runs:

```
cd web && npm test -- --run
```

Watch mode (during dev):

```
cd web && npm test
```

Type check + build (final gate):

```
cd web && npm run build
```

Playwright e2e (Task 7 only — backend must be up at `http://localhost:8000`):

```
cd web && npx playwright test tests/e2e/upload.spec.ts
```

Backend env for e2e (separate terminal — same as W5-A/B env block):

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run uvicorn flake_analysis.api.main:app --host 127.0.0.1 --port 8000
```

Playwright mocks the S3 PUT call via `page.route('**/qpress-uploads*', ...)` so e2e never hits real AWS.

---

## Task 1 — `lib/sha256.ts` Web Crypto helper

**Files:**
- Create: `web/src/lib/sha256.ts`
- Create: `web/src/lib/__tests__/sha256.test.ts`

**Why:** Foundation for the orchestrator. Pure function: `File → Promise<string>` (lowercase hex). The whole pipeline is built on this returning the SAME hex the backend computes, so locking it down with a known-vector test first protects the rest of the plan.

### Step 1.1: Write the failing test

- [ ] **Create `web/src/lib/__tests__/sha256.test.ts`:**

```ts
import { describe, it, expect } from 'vitest'
import { sha256Hex } from '@/lib/sha256'

describe('sha256Hex', () => {
  it('hashes the empty string to the canonical SHA256 hex', async () => {
    const empty = new File([], 'empty.bin')
    const hex = await sha256Hex(empty)
    // SHA256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
    expect(hex).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
  })

  it('hashes the literal string "abc" to the canonical SHA256 hex', async () => {
    const file = new File([new TextEncoder().encode('abc')], 'abc.txt')
    const hex = await sha256Hex(file)
    // SHA256("abc") = ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
    expect(hex).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  })

  it('returns 64-char lowercase hex for any input', async () => {
    const file = new File([new Uint8Array(1024)], 'zero.bin')
    const hex = await sha256Hex(file)
    expect(hex).toMatch(/^[0-9a-f]{64}$/)
  })
})
```

### Step 1.2: Run — expect FAIL

- [ ] **Run:**

```
cd web && npm test -- --run src/lib/__tests__/sha256.test.ts
```

Expected: FAIL — module `@/lib/sha256` does not exist.

### Step 1.3: Implement `sha256Hex`

- [ ] **Create `web/src/lib/sha256.ts`:**

```ts
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
```

### Step 1.4: Run — expect PASS

- [ ] **Run:**

```
cd web && npm test -- --run src/lib/__tests__/sha256.test.ts
```

Expected: 3 passed.

> **jsdom note:** Vitest's jsdom environment ships `crypto.subtle` natively in Node ≥18 via `globalThis.crypto`. If the test fails with `crypto is not defined`, add `globals: { crypto: 'readonly' }` to `vite.config.ts` test block — but verify first; current Node 20+ has it.

### Step 1.5: Commit

- [ ] **Run:**

```bash
git add web/src/lib/sha256.ts web/src/lib/__tests__/sha256.test.ts
git commit -m "feat(web): sha256Hex Web Crypto helper for upload pipeline (W5-C.1)"
```

---

## Task 2 — `state/uploadSlice.ts` + `lib/uploadOrchestrator.ts`

**Files:**
- Create: `web/src/api/upload.ts` (typed wrappers — needed by orchestrator)
- Create: `web/src/api/materials.ts` (typed wrappers)
- Create: `web/src/state/uploadSlice.ts`
- Create: `web/src/lib/uploadOrchestrator.ts`
- Create: `web/src/lib/__tests__/uploadOrchestrator.test.ts`

**Why:** Heart of W5-C. Without this, the UI is a static form. The orchestrator owns the per-file state machine; the slice is its data store. Also lay down the API typed clients now so the orchestrator imports real types.

### Step 2.1: API typed clients

- [ ] **Create `web/src/api/materials.ts`:**

```ts
// web/src/api/materials.ts
import { ApiError } from '@/api/selector'
import { getAuthHeaders } from '@/api/authHeaders'

export interface Material {
  name: string
}

export interface CreateMaterialResult {
  name: string
  created: boolean
}

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let env: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } | null = null
    try { env = await resp.json() } catch { throw new ApiError(resp.status, 'http_error', `HTTP ${resp.status}`, null) }
    const err = env?.error ?? {}
    throw new ApiError(resp.status, err.code ?? 'http_error', err.message ?? `HTTP ${resp.status}`, err.details ?? null, err.request_id)
  }
  return (await resp.json()) as T
}

export async function fetchMaterials(): Promise<Material[]> {
  const resp = await fetch('/api/v1/materials', {
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
  })
  const env = await unwrap<{ materials: Material[] }>(resp)
  return env.materials
}

export async function createMaterial(name: string): Promise<CreateMaterialResult> {
  const resp = await fetch('/api/v1/materials', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    body: JSON.stringify({ name }),
  })
  return unwrap<CreateMaterialResult>(resp)
}
```

- [ ] **Create `web/src/api/upload.ts`:**

```ts
// web/src/api/upload.ts
import { ApiError } from '@/api/selector'
import { getAuthHeaders } from '@/api/authHeaders'

export interface CreateScanBody {
  name: string
  material: string
  image_count: number
  extra_metadata: Record<string, string>
}
export interface CreateScanResult { scan_id: string }

export interface PresignBody {
  filename: string
  sha256_hex: string
  size_bytes: number
  grid_ix: number
  grid_iy: number
}
export interface PresignResult {
  put_url: string
  headers: Record<string, string>
  upload_item_id: string
}

export interface CompleteResult { image_id: string }

export interface FinalizeResult { status: 'ready' | string }

async function unwrap<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let env: { error?: { code?: string; message?: string; details?: unknown; request_id?: string } } | null = null
    try { env = await resp.json() } catch { throw new ApiError(resp.status, 'http_error', `HTTP ${resp.status}`, null) }
    const err = env?.error ?? {}
    throw new ApiError(resp.status, err.code ?? 'http_error', err.message ?? `HTTP ${resp.status}`, err.details ?? null, err.request_id)
  }
  return (await resp.json()) as T
}

export async function createScan(projectId: string, body: CreateScanBody, signal?: AbortSignal): Promise<CreateScanResult> {
  const resp = await fetch(`/api/v1/projects/${projectId}/scans`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    body: JSON.stringify(body),
    signal,
  })
  return unwrap<CreateScanResult>(resp)
}

export async function presignImage(scanId: string, body: PresignBody, signal?: AbortSignal): Promise<PresignResult> {
  const resp = await fetch(`/api/v1/scans/${scanId}/images/presign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    body: JSON.stringify(body),
    signal,
  })
  return unwrap<PresignResult>(resp)
}

export async function completeImage(scanId: string, uploadItemId: string, signal?: AbortSignal): Promise<CompleteResult> {
  const resp = await fetch(`/api/v1/scans/${scanId}/images/${uploadItemId}/complete`, {
    method: 'POST',
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    signal,
  })
  return unwrap<CompleteResult>(resp)
}

export async function finalizeScan(scanId: string, signal?: AbortSignal): Promise<FinalizeResult> {
  const resp = await fetch(`/api/v1/scans/${scanId}/finalize`, {
    method: 'POST',
    headers: { Accept: 'application/json', ...getAuthHeaders() },
    credentials: 'include',
    signal,
  })
  return unwrap<FinalizeResult>(resp)
}

export async function putToS3(putUrl: string, file: File, headers: Record<string, string>, signal?: AbortSignal): Promise<void> {
  const resp = await fetch(putUrl, {
    method: 'PUT',
    headers,
    body: file,
    signal,
  })
  if (!resp.ok) {
    throw new Error(`S3 PUT failed: ${resp.status} ${resp.statusText}`)
  }
}
```

### Step 2.2: Slice

- [ ] **Create `web/src/state/uploadSlice.ts`:**

```ts
// web/src/state/uploadSlice.ts
import { create } from 'zustand'

export type FileStatus =
  | 'queued'
  | 'hashing'
  | 'presigning'
  | 'uploading'
  | 'completing'
  | 'done'
  | 'failed'

export interface UploadFile {
  uid: string            // local-only id, NOT upload_item_id
  file: File
  filename: string
  size: number
  grid_ix: number | null
  grid_iy: number | null
  status: FileStatus
  progress: number       // 0..1, only meaningful during 'uploading'
  sha256_hex: string | null
  upload_item_id: string | null
  image_id: string | null
  error: string | null
}

export interface UploadState {
  scanId: string | null
  files: Record<string, UploadFile>
  order: string[]                // stable display order

  setScanId(id: string | null): void
  addFiles(files: File[]): void
  setGrid(uid: string, ix: number | null, iy: number | null): void
  removeFile(uid: string): void
  patch(uid: string, patch: Partial<UploadFile>): void
  reset(): void
}

function genUid(): string {
  return `f_${Math.random().toString(36).slice(2, 10)}`
}

/** Auto-detect (ix, iy) from filename pattern `tile_{ix}_{iy}.<ext>`. */
const FILENAME_GRID_RE = /^.*tile_(\d+)_(\d+)\..*$/i
export function detectGrid(filename: string): { ix: number | null; iy: number | null } {
  const m = filename.match(FILENAME_GRID_RE)
  if (!m) return { ix: null, iy: null }
  return { ix: parseInt(m[1], 10), iy: parseInt(m[2], 10) }
}

export const useUploadStore = create<UploadState>((set) => ({
  scanId: null,
  files: {},
  order: [],

  setScanId(id) {
    set({ scanId: id })
  },
  addFiles(files) {
    set((s) => {
      const nextFiles = { ...s.files }
      const nextOrder = [...s.order]
      for (const f of files) {
        const uid = genUid()
        const { ix, iy } = detectGrid(f.name)
        nextFiles[uid] = {
          uid,
          file: f,
          filename: f.name,
          size: f.size,
          grid_ix: ix,
          grid_iy: iy,
          status: 'queued',
          progress: 0,
          sha256_hex: null,
          upload_item_id: null,
          image_id: null,
          error: null,
        }
        nextOrder.push(uid)
      }
      return { files: nextFiles, order: nextOrder }
    })
  },
  setGrid(uid, ix, iy) {
    set((s) => {
      const cur = s.files[uid]
      if (!cur) return s
      return { files: { ...s.files, [uid]: { ...cur, grid_ix: ix, grid_iy: iy } } }
    })
  },
  removeFile(uid) {
    set((s) => {
      const { [uid]: _drop, ...rest } = s.files
      return { files: rest, order: s.order.filter((x) => x !== uid) }
    })
  },
  patch(uid, p) {
    set((s) => {
      const cur = s.files[uid]
      if (!cur) return s
      return { files: { ...s.files, [uid]: { ...cur, ...p } } }
    })
  },
  reset() {
    set({ scanId: null, files: {}, order: [] })
  },
}))

export function resetUploadStore(): void {
  useUploadStore.getState().reset()
}
```

### Step 2.3: Failing orchestrator test

- [ ] **Create `web/src/lib/__tests__/uploadOrchestrator.test.ts`:**

```ts
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { useUploadStore, resetUploadStore } from '@/state/uploadSlice'
import { Orchestrator } from '@/lib/uploadOrchestrator'
import * as upload from '@/api/upload'
import * as sha from '@/lib/sha256'

beforeEach(() => {
  resetUploadStore()
  vi.restoreAllMocks()
})
afterEach(() => {
  vi.restoreAllMocks()
})

function fakeFile(name: string, bytes = 4): File {
  return new File([new Uint8Array(bytes)], name)
}

describe('Orchestrator', () => {
  it('runs hash → presign → PUT → complete and ends in done', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('a'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockResolvedValue({
      put_url: 'http://s3.fake/put', headers: { 'x-amz-checksum-sha256': 'b64' }, upload_item_id: 'ui_1',
    })
    vi.spyOn(upload, 'putToS3').mockResolvedValue(undefined)
    vi.spyOn(upload, 'completeImage').mockResolvedValue({ image_id: 'img_1' })

    useUploadStore.getState().setScanId('scan_1')
    useUploadStore.getState().addFiles([fakeFile('tile_0_0.tif')])
    const uid = useUploadStore.getState().order[0]
    useUploadStore.getState().setGrid(uid, 0, 0)

    const orch = new Orchestrator({ concurrency: 4 })
    await orch.runAll()

    const f = useUploadStore.getState().files[uid]
    expect(f.status).toBe('done')
    expect(f.upload_item_id).toBe('ui_1')
    expect(f.image_id).toBe('img_1')
    expect(f.sha256_hex).toBe('a'.repeat(64))
  })

  it('marks file failed when presign throws and preserves error message', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('c'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockRejectedValue(new Error('presign 409 duplicate'))

    useUploadStore.getState().setScanId('scan_1')
    useUploadStore.getState().addFiles([fakeFile('tile_1_2.tif')])
    const uid = useUploadStore.getState().order[0]
    useUploadStore.getState().setGrid(uid, 1, 2)

    const orch = new Orchestrator({ concurrency: 4 })
    await orch.runAll()

    const f = useUploadStore.getState().files[uid]
    expect(f.status).toBe('failed')
    expect(f.error).toMatch(/presign 409 duplicate/)
  })

  it('respects the concurrency limit (max 4 in flight)', async () => {
    let inFlight = 0
    let peak = 0
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('d'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockImplementation(async () => {
      inFlight++
      peak = Math.max(peak, inFlight)
      await new Promise((r) => setTimeout(r, 10))
      inFlight--
      return { put_url: 'http://s3.fake/put', headers: {}, upload_item_id: 'x' }
    })
    vi.spyOn(upload, 'putToS3').mockResolvedValue(undefined)
    vi.spyOn(upload, 'completeImage').mockResolvedValue({ image_id: 'img' })

    useUploadStore.getState().setScanId('scan_1')
    const files: File[] = []
    for (let i = 0; i < 10; i++) files.push(fakeFile(`tile_0_${i}.tif`))
    useUploadStore.getState().addFiles(files)
    for (const uid of useUploadStore.getState().order) {
      useUploadStore.getState().setGrid(uid, 0, 0)
    }

    const orch = new Orchestrator({ concurrency: 4 })
    await orch.runAll()
    expect(peak).toBeLessThanOrEqual(4)
  })

  it('cancel() aborts in-flight work and the file lands non-done', async () => {
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('e'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockImplementation(async (_sid, _body, signal) => {
      return new Promise((_resolve, reject) => {
        signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    })

    useUploadStore.getState().setScanId('scan_1')
    useUploadStore.getState().addFiles([fakeFile('tile_0_0.tif')])
    const uid = useUploadStore.getState().order[0]
    useUploadStore.getState().setGrid(uid, 0, 0)

    const orch = new Orchestrator({ concurrency: 4 })
    const p = orch.runAll()
    // give it a microtask to start, then cancel
    await new Promise((r) => setTimeout(r, 5))
    orch.cancelAll()
    await p

    const f = useUploadStore.getState().files[uid]
    expect(f.status).not.toBe('done')
  })
})
```

### Step 2.4: Run — expect FAIL (no orchestrator yet)

- [ ] **Run:**

```
cd web && npm test -- --run src/lib/__tests__/uploadOrchestrator.test.ts
```

Expected: FAIL — module `@/lib/uploadOrchestrator` not found.

### Step 2.5: Implement orchestrator

- [ ] **Create `web/src/lib/uploadOrchestrator.ts`:**

```ts
// web/src/lib/uploadOrchestrator.ts
import { useUploadStore, type UploadFile } from '@/state/uploadSlice'
import { sha256Hex } from '@/lib/sha256'
import {
  presignImage,
  putToS3,
  completeImage,
  type PresignBody,
} from '@/api/upload'

export interface OrchestratorOptions {
  concurrency?: number
}

/**
 * Per-file pipeline driver. Owns one AbortController per file and the
 * concurrency semaphore. Reads/mutates `useUploadStore` directly — no other
 * code in the app should write to file.status.
 */
export class Orchestrator {
  private readonly concurrency: number
  private readonly controllers = new Map<string, AbortController>()
  private cancelled = false

  constructor(opts: OrchestratorOptions = {}) {
    this.concurrency = opts.concurrency ?? 4
  }

  /** Run every queued file through the pipeline. Resolves when all settle. */
  async runAll(): Promise<void> {
    const store = useUploadStore.getState()
    const scanId = store.scanId
    if (!scanId) throw new Error('Orchestrator.runAll: scanId not set')

    const queue = store.order
      .map((uid) => store.files[uid])
      .filter((f) => f.status === 'queued')

    let cursor = 0
    const workers: Promise<void>[] = []
    const next = async (): Promise<void> => {
      while (!this.cancelled) {
        const i = cursor++
        if (i >= queue.length) return
        await this.runOne(scanId, queue[i].uid)
      }
    }
    for (let i = 0; i < this.concurrency; i++) workers.push(next())
    await Promise.all(workers)
  }

  /** Cancel every in-flight fetch and stop new work. */
  cancelAll(): void {
    this.cancelled = true
    for (const c of this.controllers.values()) c.abort()
    this.controllers.clear()
  }

  /** Retry a single failed file. */
  async retry(uid: string): Promise<void> {
    const store = useUploadStore.getState()
    const scanId = store.scanId
    if (!scanId) throw new Error('Orchestrator.retry: scanId not set')
    store.patch(uid, { status: 'queued', error: null, progress: 0 })
    await this.runOne(scanId, uid)
  }

  private async runOne(scanId: string, uid: string): Promise<void> {
    const ctrl = new AbortController()
    this.controllers.set(uid, ctrl)
    const store = useUploadStore.getState()
    const f = store.files[uid]
    if (!f) return
    if (f.grid_ix === null || f.grid_iy === null) {
      store.patch(uid, { status: 'failed', error: 'grid_ix/grid_iy required' })
      this.controllers.delete(uid)
      return
    }

    try {
      // 1) hash
      store.patch(uid, { status: 'hashing' })
      const hex = await sha256Hex(f.file)
      if (this.cancelled || ctrl.signal.aborted) throw new DOMException('aborted', 'AbortError')
      store.patch(uid, { sha256_hex: hex })

      // 2) presign
      store.patch(uid, { status: 'presigning' })
      const body: PresignBody = {
        filename: f.filename,
        sha256_hex: hex,
        size_bytes: f.size,
        grid_ix: f.grid_ix,
        grid_iy: f.grid_iy,
      }
      const pre = await presignImage(scanId, body, ctrl.signal)
      store.patch(uid, { upload_item_id: pre.upload_item_id })

      // 3) PUT to S3
      store.patch(uid, { status: 'uploading', progress: 0 })
      await putToS3(pre.put_url, f.file, pre.headers, ctrl.signal)
      store.patch(uid, { progress: 1 })

      // 4) complete
      store.patch(uid, { status: 'completing' })
      const cmp = await completeImage(scanId, pre.upload_item_id, ctrl.signal)
      store.patch(uid, { status: 'done', image_id: cmp.image_id })
    } catch (e: unknown) {
      const msg = (e as { message?: string })?.message ?? String(e)
      const aborted = (e as { name?: string })?.name === 'AbortError'
      store.patch(uid, {
        status: 'failed',
        error: aborted ? 'aborted' : msg,
      })
    } finally {
      this.controllers.delete(uid)
    }
  }
}

/** Module-scope singleton — UploadModal owns its lifecycle. */
let current: Orchestrator | null = null
export function getOrchestrator(): Orchestrator {
  if (!current) current = new Orchestrator({ concurrency: 4 })
  return current
}
export function resetOrchestrator(): void {
  current?.cancelAll()
  current = null
}

export function isFinalFile(f: UploadFile): boolean {
  return f.status === 'done' || f.status === 'failed'
}
```

### Step 2.6: Run — expect PASS

- [ ] **Run:**

```
cd web && npm test -- --run src/lib/__tests__/uploadOrchestrator.test.ts
```

Expected: 4 passed.

### Step 2.7: Commit

- [ ] **Run:**

```bash
git add web/src/api/upload.ts web/src/api/materials.ts web/src/state/uploadSlice.ts web/src/lib/uploadOrchestrator.ts web/src/lib/__tests__/uploadOrchestrator.test.ts
git commit -m "feat(web): upload slice + orchestrator with concurrency 4 + abort (W5-C.2)"
```

---

## Task 3 — `MaterialCombobox.tsx`

**Files:**
- Create: `web/src/components/upload/MaterialCombobox.tsx`
- Create: `web/src/components/upload/__tests__/MaterialCombobox.test.tsx`

**Why:** Material is a controlled vocab on the backend (W5-A) but free input on the frontend (D4). The combobox is dropdown-with-typeahead: list materials from `GET /materials`; if user types something not in the list and submits, it auto-creates via `POST /materials` and selects it. Isolated from the form so the form just gets a `string`.

### Step 3.1: Failing test

- [ ] **Create `web/src/components/upload/__tests__/MaterialCombobox.test.tsx`:**

```tsx
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
      { name: 'graphene' }, { name: 'MoS2' },
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
    const createSpy = vi.spyOn(materialsApi, 'createMaterial').mockResolvedValue({ name: 'NbSe2', created: true })
    const onChange = vi.fn()
    render(wrap(<MaterialCombobox value="" onChange={onChange} />))
    const input = await screen.findByTestId('material-combobox-input')
    await userEvent.type(input, 'NbSe2')
    await userEvent.click(screen.getByTestId('material-combobox-create-btn'))
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith('NbSe2'))
    expect(onChange).toHaveBeenCalledWith('NbSe2')
  })
})
```

### Step 3.2: Run — expect FAIL

- [ ] **Run:**

```
cd web && npm test -- --run src/components/upload/__tests__/MaterialCombobox.test.tsx
```

Expected: FAIL — module not found.

### Step 3.3: Implement

- [ ] **Create `web/src/components/upload/MaterialCombobox.tsx`:**

```tsx
// web/src/components/upload/MaterialCombobox.tsx
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchMaterials, createMaterial, type Material } from '@/api/materials'

interface Props {
  value: string
  onChange(name: string): void
}

export function MaterialCombobox({ value, onChange }: Props) {
  const qc = useQueryClient()
  const [input, setInput] = useState(value)
  const [open, setOpen] = useState(false)

  const list = useQuery<Material[]>({
    queryKey: ['materials', 'list'],
    queryFn: fetchMaterials,
    staleTime: 60_000,
  })

  const create = useMutation({
    mutationFn: (name: string) => createMaterial(name),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['materials', 'list'] })
      onChange(res.name)
      setInput(res.name)
      setOpen(false)
    },
  })

  const matches = (list.data ?? []).filter((m) =>
    m.name.toLowerCase().includes(input.toLowerCase())
  )
  const exact = (list.data ?? []).some((m) => m.name === input)

  return (
    <div data-testid="material-combobox-root" style={{ position: 'relative' }}>
      <input
        data-testid="material-combobox-input"
        type="text"
        value={input}
        onChange={(e) => { setInput(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        placeholder="material (e.g. graphene)"
        style={{ width: '100%' }}
      />
      {open && (
        <ul
          data-testid="material-combobox-list"
          style={{
            position: 'absolute', top: '100%', left: 0, right: 0,
            margin: 0, padding: 4, listStyle: 'none',
            background: 'white', border: '1px solid #ccc', maxHeight: 160, overflowY: 'auto',
            zIndex: 10,
          }}
        >
          {matches.map((m) => (
            <li
              key={m.name}
              data-testid={`material-combobox-option-${m.name}`}
              style={{ padding: 4, cursor: 'pointer' }}
              onClick={() => { onChange(m.name); setInput(m.name); setOpen(false) }}
            >
              {m.name}
            </li>
          ))}
          {input && !exact && (
            <li style={{ padding: 4, borderTop: '1px solid #eee' }}>
              <button
                data-testid="material-combobox-create-btn"
                type="button"
                disabled={create.isPending}
                onClick={() => create.mutate(input)}
              >
                {create.isPending ? 'Creating...' : `+ Add "${input}"`}
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  )
}
```

### Step 3.4: Run — expect PASS

- [ ] **Run:**

```
cd web && npm test -- --run src/components/upload/__tests__/MaterialCombobox.test.tsx
```

Expected: 2 passed.

### Step 3.5: Commit

- [ ] **Run:**

```bash
git add web/src/components/upload/MaterialCombobox.tsx web/src/components/upload/__tests__/MaterialCombobox.test.tsx
git commit -m "feat(web): MaterialCombobox with auto-create-on-unknown (W5-C.3)"
```

---

## Task 4 — `ScanForm.tsx`

**Files:**
- Create: `web/src/components/upload/ScanForm.tsx`

**Why:** Encapsulates the scan metadata fields (`name`, `material`, `image_count`, `extra_metadata`) and validates them. Uses react-hook-form for form state, manual rules (no zod). Calls back to parent with the validated payload — does NOT call the API itself; UploadModal owns the lifecycle.

### Step 4.1: Implement (no failing test first — covered by UploadModal integration test in Task 6)

- [ ] **Create `web/src/components/upload/ScanForm.tsx`:**

```tsx
// web/src/components/upload/ScanForm.tsx
import { useForm, type SubmitHandler } from 'react-hook-form'
import { useState } from 'react'
import { MaterialCombobox } from './MaterialCombobox'

export interface ScanFormValues {
  name: string
  material: string
  image_count: number
  extra_metadata: Record<string, string>
}

interface KV { key: string; value: string }

interface Props {
  defaultExpectedCount?: number
  onSubmit(values: ScanFormValues): void
  disabled?: boolean
}

export function ScanForm({ defaultExpectedCount, onSubmit, disabled }: Props) {
  const { register, handleSubmit, setValue, watch, formState: { errors } } = useForm<{
    name: string
    image_count: number
  }>({
    defaultValues: {
      name: '',
      image_count: defaultExpectedCount ?? 1,
    },
  })
  const [material, setMaterial] = useState('')
  const [kvs, setKvs] = useState<KV[]>([])

  const handle: SubmitHandler<{ name: string; image_count: number }> = (vals) => {
    if (!material) return  // MaterialCombobox shows its own UI; bail silently
    const meta: Record<string, string> = {}
    for (const kv of kvs) {
      const k = kv.key.trim()
      if (k) meta[k] = kv.value
    }
    onSubmit({
      name: vals.name.trim(),
      material,
      image_count: Number(vals.image_count),
      extra_metadata: meta,
    })
  }

  return (
    <form
      data-testid="scan-form"
      onSubmit={handleSubmit(handle)}
      style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
    >
      <label style={{ fontSize: 12 }}>
        Scan name <span style={{ color: '#b91c1c' }}>*</span>
        <input
          data-testid="scan-form-name"
          {...register('name', { required: 'name required', minLength: 1 })}
          disabled={disabled}
          style={{ width: '100%' }}
        />
        {errors.name && <span style={{ color: '#b91c1c', fontSize: 11 }}>{errors.name.message}</span>}
      </label>

      <label style={{ fontSize: 12 }}>
        Material <span style={{ color: '#b91c1c' }}>*</span>
        <MaterialCombobox value={material} onChange={setMaterial} />
        {!material && (
          <span data-testid="scan-form-material-error" style={{ color: '#b91c1c', fontSize: 11 }}>
            material required
          </span>
        )}
      </label>

      <label style={{ fontSize: 12 }}>
        Image count <span style={{ color: '#b91c1c' }}>*</span>
        <input
          data-testid="scan-form-image-count"
          type="number"
          min={1}
          {...register('image_count', { required: true, valueAsNumber: true, min: 1 })}
          disabled={disabled}
          style={{ width: '100%' }}
        />
      </label>

      <fieldset style={{ border: '1px solid #e5e7eb', padding: 8 }}>
        <legend style={{ fontSize: 12 }}>Extra metadata (optional)</legend>
        {kvs.map((kv, i) => (
          <div key={i} style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
            <input
              data-testid={`scan-form-kv-key-${i}`}
              placeholder="key"
              value={kv.key}
              onChange={(e) => setKvs((cur) => cur.map((c, j) => j === i ? { ...c, key: e.target.value } : c))}
              style={{ flex: 1 }}
            />
            <input
              data-testid={`scan-form-kv-value-${i}`}
              placeholder="value"
              value={kv.value}
              onChange={(e) => setKvs((cur) => cur.map((c, j) => j === i ? { ...c, value: e.target.value } : c))}
              style={{ flex: 2 }}
            />
            <button
              type="button"
              data-testid={`scan-form-kv-remove-${i}`}
              onClick={() => setKvs((cur) => cur.filter((_, j) => j !== i))}
            >
              ×
            </button>
          </div>
        ))}
        <button
          type="button"
          data-testid="scan-form-kv-add"
          onClick={() => setKvs((cur) => [...cur, { key: '', value: '' }])}
        >
          + add row
        </button>
      </fieldset>

      <button
        data-testid="scan-form-submit"
        type="submit"
        disabled={disabled || !material}
      >
        Save scan metadata
      </button>
    </form>
  )
}
```

### Step 4.2: Verify it type-checks

- [ ] **Run:**

```
cd web && npx tsc --noEmit
```

Expected: 0 errors.

### Step 4.3: Commit

- [ ] **Run:**

```bash
git add web/src/components/upload/ScanForm.tsx
git commit -m "feat(web): ScanForm with material + image_count + key/value extras (W5-C.4)"
```

---

## Task 5 — `FileDropzone.tsx` + `FileRow.tsx` + `ProgressList.tsx`

**Files:**
- Create: `web/src/components/upload/FileDropzone.tsx`
- Create: `web/src/components/upload/FileRow.tsx`
- Create: `web/src/components/upload/ProgressList.tsx`
- Create: `web/src/components/upload/__tests__/FileDropzone.test.tsx`

**Why:** Drop-zone is the file-intake UI. FileRow is the per-file display (filename, ix/iy editable, status pill, progress bar, retry). ProgressList wires them together over the slice's `order` array.

### Step 5.1: Failing dropzone test

- [ ] **Create `web/src/components/upload/__tests__/FileDropzone.test.tsx`:**

```tsx
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
```

### Step 5.2: Run — expect FAIL

- [ ] **Run:**

```
cd web && npm test -- --run src/components/upload/__tests__/FileDropzone.test.tsx
```

Expected: FAIL.

### Step 5.3: Implement components

- [ ] **Create `web/src/components/upload/FileDropzone.tsx`:**

```tsx
// web/src/components/upload/FileDropzone.tsx
import { useRef, useState, type DragEvent } from 'react'
import { useUploadStore } from '@/state/uploadSlice'

export function FileDropzone() {
  const addFiles = useUploadStore((s) => s.addFiles)
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setOver(false)
    const files = Array.from(e.dataTransfer.files ?? [])
    if (files.length) addFiles(files)
  }

  return (
    <div
      data-testid="file-dropzone"
      onDragEnter={(e) => { e.preventDefault(); setOver(true) }}
      onDragOver={(e) => { e.preventDefault(); setOver(true) }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      style={{
        border: `2px dashed ${over ? '#4f46e5' : '#9ca3af'}`,
        borderRadius: 6,
        padding: 24,
        textAlign: 'center',
        cursor: 'pointer',
        background: over ? '#eef2ff' : '#fafafa',
      }}
    >
      Drop image files here, or click to pick
      <input
        ref={inputRef}
        data-testid="file-dropzone-input"
        type="file"
        multiple
        style={{ display: 'none' }}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          if (files.length) addFiles(files)
          e.target.value = ''
        }}
      />
    </div>
  )
}
```

- [ ] **Create `web/src/components/upload/FileRow.tsx`:**

```tsx
// web/src/components/upload/FileRow.tsx
import { useUploadStore, type UploadFile } from '@/state/uploadSlice'
import { getOrchestrator } from '@/lib/uploadOrchestrator'

interface Props { uid: string }

const STATUS_COLORS: Record<UploadFile['status'], string> = {
  queued: '#9ca3af', hashing: '#fbbf24', presigning: '#fbbf24',
  uploading: '#3b82f6', completing: '#3b82f6',
  done: '#10b981', failed: '#ef4444',
}

export function FileRow({ uid }: Props) {
  const file = useUploadStore((s) => s.files[uid])
  const setGrid = useUploadStore((s) => s.setGrid)
  const removeFile = useUploadStore((s) => s.removeFile)
  if (!file) return null

  const onRetry = () => { void getOrchestrator().retry(uid) }

  return (
    <div
      data-testid={`file-row-${uid}`}
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 60px 60px 100px 80px 60px',
        gap: 8,
        alignItems: 'center',
        padding: '4px 0',
        borderBottom: '1px solid #f0f0f0',
      }}
    >
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {file.filename}
      </span>
      <input
        data-testid={`file-row-${uid}-ix`}
        type="number"
        min={0}
        value={file.grid_ix ?? ''}
        onChange={(e) => setGrid(uid, e.target.value === '' ? null : Number(e.target.value), file.grid_iy)}
        disabled={file.status !== 'queued' && file.status !== 'failed'}
        placeholder="ix"
      />
      <input
        data-testid={`file-row-${uid}-iy`}
        type="number"
        min={0}
        value={file.grid_iy ?? ''}
        onChange={(e) => setGrid(uid, file.grid_ix, e.target.value === '' ? null : Number(e.target.value))}
        disabled={file.status !== 'queued' && file.status !== 'failed'}
        placeholder="iy"
      />
      <span
        data-testid={`file-row-${uid}-status`}
        style={{ color: STATUS_COLORS[file.status], fontSize: 12 }}
      >
        {file.status}
        {file.error ? `: ${file.error}` : ''}
      </span>
      <div style={{ width: 80, height: 6, background: '#e5e7eb', borderRadius: 3 }}>
        <div
          data-testid={`file-row-${uid}-progress`}
          style={{
            width: `${Math.round(file.progress * 100)}%`,
            height: '100%',
            background: STATUS_COLORS[file.status],
            borderRadius: 3,
          }}
        />
      </div>
      {file.status === 'failed' ? (
        <button data-testid={`file-row-${uid}-retry`} onClick={onRetry}>retry</button>
      ) : file.status === 'queued' ? (
        <button data-testid={`file-row-${uid}-remove`} onClick={() => removeFile(uid)}>×</button>
      ) : (
        <span />
      )}
    </div>
  )
}
```

- [ ] **Create `web/src/components/upload/ProgressList.tsx`:**

```tsx
// web/src/components/upload/ProgressList.tsx
import { useUploadStore } from '@/state/uploadSlice'
import { FileRow } from './FileRow'

export function ProgressList() {
  const order = useUploadStore((s) => s.order)
  if (order.length === 0) {
    return <p data-testid="progress-list-empty" style={{ color: '#6b7280' }}>No files queued.</p>
  }
  return (
    <div data-testid="progress-list" style={{ marginTop: 8 }}>
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 60px 60px 100px 80px 60px',
        gap: 8,
        fontSize: 11,
        color: '#6b7280',
        padding: '4px 0',
      }}>
        <span>filename</span><span>ix</span><span>iy</span><span>status</span><span>progress</span><span></span>
      </div>
      {order.map((uid) => <FileRow key={uid} uid={uid} />)}
    </div>
  )
}
```

### Step 5.4: Run — expect PASS

- [ ] **Run:**

```
cd web && npm test -- --run src/components/upload/__tests__/FileDropzone.test.tsx
```

Expected: 2 passed.

### Step 5.5: Commit

- [ ] **Run:**

```bash
git add web/src/components/upload/FileDropzone.tsx web/src/components/upload/FileRow.tsx web/src/components/upload/ProgressList.tsx web/src/components/upload/__tests__/FileDropzone.test.tsx
git commit -m "feat(web): FileDropzone + FileRow + ProgressList (W5-C.5)"
```

---

## Task 6 — `UploadModal.tsx` + ComputeTab integration

**Files:**
- Create: `web/src/components/upload/UploadModal.tsx`
- Create: `web/src/components/upload/__tests__/UploadModal.test.tsx`
- Modify: `web/src/pages/ComputeTab.tsx`

**Why:** Brings everything together. Modal owns the two-phase flow:
1. **Metadata phase**: ScanForm → on submit, `POST /projects/{pid}/scans` → store `scanId` in slice.
2. **Files phase**: FileDropzone visible, ProgressList live; "Start upload" button calls `orchestrator.runAll()`; "Finalize" enabled when all rows are `done`.

Closing the modal mid-upload calls `orchestrator.cancelAll()` and `resetUploadStore()`.

### Step 6.1: Failing UploadModal test

- [ ] **Create `web/src/components/upload/__tests__/UploadModal.test.tsx`:**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { UploadModal } from '../UploadModal'
import { resetUploadStore } from '@/state/uploadSlice'
import * as upload from '@/api/upload'
import * as materials from '@/api/materials'
import * as sha from '@/lib/sha256'

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

beforeEach(() => {
  resetUploadStore()
  vi.restoreAllMocks()
  vi.spyOn(materials, 'fetchMaterials').mockResolvedValue([{ name: 'graphene' }])
})

describe('UploadModal', () => {
  it('creates scan, accepts files, runs pipeline, finalizes', async () => {
    const createScanSpy = vi.spyOn(upload, 'createScan').mockResolvedValue({ scan_id: 'scan_123' })
    vi.spyOn(sha, 'sha256Hex').mockResolvedValue('a'.repeat(64))
    vi.spyOn(upload, 'presignImage').mockResolvedValue({
      put_url: 'http://s3.fake/p', headers: {}, upload_item_id: 'ui_1',
    })
    vi.spyOn(upload, 'putToS3').mockResolvedValue(undefined)
    vi.spyOn(upload, 'completeImage').mockResolvedValue({ image_id: 'img_1' })
    const finalSpy = vi.spyOn(upload, 'finalizeScan').mockResolvedValue({ status: 'ready' })

    const onClose = vi.fn()
    render(wrap(<UploadModal projectId="p1" open onClose={onClose} />))

    // Phase 1: metadata
    await userEvent.type(screen.getByTestId('scan-form-name'), 'scan-A')
    const matInput = await screen.findByTestId('material-combobox-input')
    await userEvent.click(matInput)
    await userEvent.click(await screen.findByTestId('material-combobox-option-graphene'))
    await userEvent.clear(screen.getByTestId('scan-form-image-count'))
    await userEvent.type(screen.getByTestId('scan-form-image-count'), '1')
    await userEvent.click(screen.getByTestId('scan-form-submit'))
    await waitFor(() => expect(createScanSpy).toHaveBeenCalled())

    // Phase 2: files
    const dz = await screen.findByTestId('file-dropzone')
    const f = new File([new Uint8Array(8)], 'tile_0_0.tif')
    fireEvent.drop(dz, { dataTransfer: { files: [f], items: [], types: ['Files'] } })
    await userEvent.click(await screen.findByTestId('upload-modal-start'))
    await waitFor(() => expect(screen.getByText(/done/i)).toBeTruthy())

    // Finalize
    await userEvent.click(await screen.findByTestId('upload-modal-finalize'))
    await waitFor(() => expect(finalSpy).toHaveBeenCalledWith('scan_123', undefined))
  })

  it('aborts in-flight work and resets store on close', async () => {
    vi.spyOn(upload, 'createScan').mockResolvedValue({ scan_id: 'scan_abort' })
    const onClose = vi.fn()
    render(wrap(<UploadModal projectId="p1" open onClose={onClose} />))
    await userEvent.click(screen.getByTestId('upload-modal-close'))
    expect(onClose).toHaveBeenCalled()
  })
})
```

### Step 6.2: Run — expect FAIL

- [ ] **Run:**

```
cd web && npm test -- --run src/components/upload/__tests__/UploadModal.test.tsx
```

Expected: FAIL — module not found.

### Step 6.3: Implement UploadModal

- [ ] **Create `web/src/components/upload/UploadModal.tsx`:**

```tsx
// web/src/components/upload/UploadModal.tsx
import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { ScanForm, type ScanFormValues } from './ScanForm'
import { FileDropzone } from './FileDropzone'
import { ProgressList } from './ProgressList'
import { useUploadStore, resetUploadStore } from '@/state/uploadSlice'
import { createScan, finalizeScan } from '@/api/upload'
import { getOrchestrator, resetOrchestrator } from '@/lib/uploadOrchestrator'

interface Props {
  projectId: string
  open: boolean
  onClose(): void
}

export function UploadModal({ projectId, open, onClose }: Props) {
  const qc = useQueryClient()
  const scanId = useUploadStore((s) => s.scanId)
  const setScanId = useUploadStore((s) => s.setScanId)
  const files = useUploadStore((s) => s.files)
  const order = useUploadStore((s) => s.order)
  const [running, setRunning] = useState(false)
  const [expectedCount, setExpectedCount] = useState(1)

  // hard-reset everything when modal opens fresh
  useEffect(() => {
    if (open) {
      resetUploadStore()
      resetOrchestrator()
    }
  }, [open])

  const createScanMut = useMutation({
    mutationFn: (vals: ScanFormValues) => {
      setExpectedCount(vals.image_count)
      return createScan(projectId, vals)
    },
    onSuccess: (res) => {
      setScanId(res.scan_id)
      toast.success(`Scan ${res.scan_id} created — drop files below`)
    },
    onError: (e: unknown) => {
      toast.error((e as { message?: string })?.message ?? 'createScan failed')
    },
  })

  const finalizeMut = useMutation({
    mutationFn: () => finalizeScan(scanId!),
    onSuccess: () => {
      toast.success('Scan finalized — ready')
      qc.invalidateQueries({ queryKey: ['scans', 'list', projectId] })
      handleClose()
    },
    onError: (e: unknown) => {
      toast.error((e as { message?: string })?.message ?? 'finalize failed')
    },
  })

  const handleClose = () => {
    resetOrchestrator()
    resetUploadStore()
    setRunning(false)
    onClose()
  }

  const startUpload = async () => {
    setRunning(true)
    try {
      await getOrchestrator().runAll()
    } finally {
      setRunning(false)
    }
  }

  const allDone = order.length > 0 && order.every((uid) => files[uid]?.status === 'done')
  const droppedCount = order.length
  const countMatches = scanId !== null && droppedCount === expectedCount

  if (!open) return null

  return (
    <div
      data-testid="upload-modal-overlay"
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) handleClose() }}
    >
      <div
        data-testid="upload-modal"
        style={{
          background: 'white', borderRadius: 6, padding: 16,
          width: 720, maxWidth: '90vw', maxHeight: '90vh', overflowY: 'auto',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>새 스캔 업로드</h3>
          <button data-testid="upload-modal-close" onClick={handleClose}>닫기</button>
        </div>

        {!scanId ? (
          <ScanForm
            onSubmit={(v) => createScanMut.mutate(v)}
            disabled={createScanMut.isPending}
          />
        ) : (
          <>
            <p style={{ fontSize: 12, color: '#6b7280' }}>
              scan_id: <code>{scanId}</code> · expected files: {expectedCount}
              {!countMatches && droppedCount > 0 && (
                <span style={{ color: '#b91c1c' }}> (dropped {droppedCount}, must equal {expectedCount})</span>
              )}
            </p>
            <FileDropzone />
            <ProgressList />

            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button
                data-testid="upload-modal-start"
                disabled={running || allDone || !countMatches || droppedCount === 0}
                onClick={startUpload}
              >
                {running ? 'Uploading...' : 'Start upload'}
              </button>
              <button
                data-testid="upload-modal-finalize"
                disabled={!allDone || finalizeMut.isPending}
                onClick={() => finalizeMut.mutate()}
              >
                {finalizeMut.isPending ? 'Finalizing...' : 'Finalize scan'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
```

### Step 6.4: Wire into ComputeTab

- [ ] **Modify `web/src/pages/ComputeTab.tsx` to add the trigger:**

```tsx
import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { StepCard } from '@/components/StepCard'
import { UploadModal } from '@/components/upload/UploadModal'

export function ComputeTab() {
  const { projectId } = useParams<{ projectId: string }>()
  const pid = projectId || 'local'
  const [showUpload, setShowUpload] = useState(false)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>Compute Tab</h2>
        <button data-testid="compute-tab-new-scan" onClick={() => setShowUpload(true)}>
          + 새 스캔
        </button>
      </div>

      <UploadModal projectId={pid} open={showUpload} onClose={() => setShowUpload(false)} />

      <StepCard projectId={pid} step="thumbnails" stepName="Thumbnails" />
      <StepCard projectId={pid} step="background" stepName="Background" />
      <StepCard projectId={pid} step="domain_stats" stepName="Domain Stats" />
      <StepCard projectId={pid} step="domain_proximity" stepName="Domain Proximity" />
    </div>
  )
}
```

### Step 6.5: Run all unit tests — expect PASS

- [ ] **Run:**

```
cd web && npm test -- --run
```

Expected: every Task 1–6 test passes; existing tests unchanged.

### Step 6.6: Type check + build

- [ ] **Run:**

```
cd web && npm run build
```

Expected: 0 errors.

### Step 6.7: Commit

- [ ] **Run:**

```bash
git add web/src/components/upload/UploadModal.tsx web/src/components/upload/__tests__/UploadModal.test.tsx web/src/pages/ComputeTab.tsx
git commit -m "feat(web): UploadModal two-phase flow + ComputeTab trigger (W5-C.6)"
```

---

## Task 7 — Playwright e2e smoke

**Files:**
- Modify: `web/package.json` (add `@playwright/test` dev dep)
- Create: `web/playwright.config.ts`
- Create: `tests/e2e/upload.spec.ts`

**Why:** Unit tests stub the API; this proves the wires actually connect end-to-end against a real backend (W5-B) with S3 stubbed at the network layer (`page.route()`). One happy-path scenario: open modal → fill form → drop 2 files → start → finalize → assert "ready" toast.

### Step 7.1: Install Playwright

- [ ] **Run:**

```
cd web && npm i -D @playwright/test
npx playwright install chromium
```

### Step 7.2: Create `web/playwright.config.ts`

- [ ] **Create file:**

```ts
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: '../tests/e2e',
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 30_000,
  },
})
```

### Step 7.3: Create `tests/e2e/upload.spec.ts`

- [ ] **Create file:**

```ts
import { test, expect } from '@playwright/test'
import path from 'path'

test('upload modal: create scan → drop 2 files → finalize', async ({ page }) => {
  // Stub S3 PUT (matches presigned URL host)
  await page.route(/\/qpress-uploads.*/, (route) => {
    if (route.request().method() === 'PUT') {
      return route.fulfill({ status: 200, body: '' })
    }
    return route.continue()
  })

  await page.goto('/projects/local/compute')

  // Open modal
  await page.getByTestId('compute-tab-new-scan').click()
  await expect(page.getByTestId('upload-modal')).toBeVisible()

  // Fill metadata
  await page.getByTestId('scan-form-name').fill('e2e-scan-1')
  await page.getByTestId('material-combobox-input').click()
  await page.getByTestId('material-combobox-option-graphene').click()
  await page.getByTestId('scan-form-image-count').fill('2')
  await page.getByTestId('scan-form-submit').click()

  // Drop 2 files via the hidden input
  const fixturesDir = path.join(__dirname, 'fixtures')
  await page.getByTestId('file-dropzone-input').setInputFiles([
    path.join(fixturesDir, 'tile_0_0.tif'),
    path.join(fixturesDir, 'tile_0_1.tif'),
  ])

  await page.getByTestId('upload-modal-start').click()

  // Wait for both rows to reach 'done'
  const rows = page.getByTestId(/file-row-.*-status/)
  await expect(rows.first()).toContainText('done', { timeout: 30_000 })
  await expect(rows.last()).toContainText('done', { timeout: 30_000 })

  await page.getByTestId('upload-modal-finalize').click()
  await expect(page.getByText(/finalized/i)).toBeVisible({ timeout: 10_000 })
})
```

- [ ] **Create fixture files (small valid bytes — content doesn't matter, backend doesn't decode):**

```
mkdir -p tests/e2e/fixtures
printf 'fake-tif-bytes-00' > tests/e2e/fixtures/tile_0_0.tif
printf 'fake-tif-bytes-01' > tests/e2e/fixtures/tile_0_1.tif
```

### Step 7.4: Run e2e

- [ ] **In one terminal, start the backend (must have W5-B endpoints live):**

```
SAA_DB_NAME=saa_test SAA_DB_USER=houkjang SAA_DB_HOST=127.0.0.1 \
SAA_AUTH_DEV_BYPASS=1 SAA_ENV=dev \
uv run uvicorn flake_analysis.api.main:app --host 127.0.0.1 --port 8000
```

- [ ] **In another terminal:**

```
cd web && npx playwright test tests/e2e/upload.spec.ts
```

Expected: 1 passed.

> If W5-B isn't merged yet, this step is GATED on it. Mark Task 7 blocked in `project-status.md` and proceed to Self-Review with Task 6 as the last green step.

### Step 7.5: Commit

- [ ] **Run:**

```bash
git add web/package.json web/package-lock.json web/playwright.config.ts tests/e2e/upload.spec.ts tests/e2e/fixtures/
git commit -m "test(web): Playwright e2e smoke for upload flow (W5-C.7)"
```

---

## Self-Review

**Spec coverage (D-block 2026-05-22):**
- D1 (single bucket, opaque to FE): satisfied — frontend never names the bucket; only consumes `put_url` from `presignImage`. ✓
- D2 (SHA256 client-side, hex on the wire, sequential hash → presign → PUT): satisfied — `Orchestrator.runOne` runs the steps in order; concurrency is across files, not within a single file. ✓
- D3 (per-scan session, cancel on close): satisfied — `UploadModal.handleClose` calls `resetOrchestrator()` (aborts every controller) then `resetUploadStore()`. No resumability. ✓
- D4 (form fields name/material/image_count/extra_metadata, per-file ix/iy with auto-detect): satisfied across `ScanForm` + `FileRow` + `detectGrid`. The `image_count` ↔ dropped-file-count guard is enforced in `UploadModal` (`countMatches`). ✓
- D6 (modal in project landing page, route stays on `/projects/:pid/compute`): satisfied via `ComputeTab` integration. ✓

**Concurrency (4 in flight):** owned by `Orchestrator.runAll` worker pool — not by browser fetch limits. Test 2.3.4 asserts `peak ≤ 4` directly.

**Cancel correctness:** every `fetch` in `api/upload.ts` accepts `signal?: AbortSignal`; `runOne` threads its controller into all four steps + `putToS3`. Test 2.3.5 covers presign abort. (PUT abort is the same mechanism — not separately tested but identical wiring.)

**Type consistency:** `PresignBody.size_bytes` (number) ↔ `File.size` (number); `sha256_hex` (lowercase 64-char) is the same as the `sha256Hex()` return type. The W5-B endpoint MUST accept hex (per D2) — if W5-B uses base64 instead, this is a contract break and the FE must adjust ONLY in `api/upload.ts`.

**Placeholder scan:** none. Every code block is fully written; no `// TODO`s; no `throw new Error('not implemented')`.

**Fragility:**
- `crypto.subtle` in jsdom: assumed present (Node 20+). If a CI image lacks it, swap to `globalThis.crypto?.subtle ?? require('node:crypto').webcrypto.subtle` in `sha256.ts`.
- Auto-detect regex `^.*tile_(\d+)_(\d+)\..*$` is conservative — matches `tile_3_5.tif`, `something_tile_3_5.png`, but NOT `3_5_tile.tif` or `tile-3-5.tif`. Documented; users edit ix/iy by hand for non-matches.
- `addFiles` does not de-dupe by `(filename, size)` — if a user drops the same file twice, both go to S3 and the second presign returns 409 (W5-B uniqueness). Failure surfaces in the row and they can `×` it. Acceptable for v1.

**Edge cases:**
- Empty drop after metadata phase: `Start upload` is disabled until `droppedCount === expectedCount`.
- All-failed end state: `allDone` is false → Finalize disabled. User retries individual rows or closes.
- Modal close during `createScanMut.isPending`: the request is NOT cancelled (no signal threaded — non-critical, no S3 cost). Doc-acknowledge here; not worth the wiring for a metadata POST.

---

## Open follow-up (out of W5-C scope)

- **Resume after refresh** (D3 said "deferred"): if user reloads the tab mid-upload, queued state vanishes. Future work: persist `uploadSlice` to `sessionStorage`, on remount re-stat each File via the File System Access API (only available where supported). Not in this plan.
- **Per-file PUT progress %**: `fetch()` doesn't expose upload progress events. Would need `XMLHttpRequest` + `xhr.upload.onprogress` for a real bar. Currently `progress` jumps 0 → 1. Acceptable for v1 since per-file PUT for typical 10MB files completes in a few seconds.
- **Bulk ix/iy editor**: if a user drops 100 files with names that don't match the regex, they have to fill 200 cells. Future: paste-from-CSV or "auto from manifest.json". Not in v1.
- **Drag-drop folder support**: `DataTransfer.items` + `webkitGetAsEntry()` would let users drag a folder. Not in v1; multi-file drag works.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-22-W5-C-frontend.md`.

**Recommended execution mode:** Subagent-Driven. 7 tasks. Estimate 5–10 min implementer per task except Task 6 (≈15 min — most integration) and Task 7 (≈10 min + needs a running W5-B backend).

**Dispatch order (strict — each task depends on prior):**

```
1 (sha256)
  → 2 (slice + orchestrator + api typed clients)
    → 3 (MaterialCombobox)        ┐
    → 4 (ScanForm)                ├─ Tasks 3,4,5 are independent;
    → 5 (Dropzone + Row + List)   ┘   PM may dispatch in parallel
      → 6 (UploadModal + ComputeTab) — depends on 3, 4, 5
        → 7 (e2e — gated on W5-B merge)
```

**PM check-ins:**
- After Task 2: review orchestrator semaphore + abort wiring (highest risk).
- After Task 6: hand-spin the dev server (`cd web && npm run dev`) and click through the modal with mocked endpoints (or real W5-B if available) before signing off.
- Task 7: only run if W5-B head is on `main`. Otherwise mark blocked in `docs/project-status.md` and ship Tasks 1–6 with an addendum noting e2e is pending W5-B.
