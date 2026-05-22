// Vitest setup: jsdom ships a stub `File` whose contents serialize as
// "[object File]" and which lacks `arrayBuffer()`. Override the global with
// Node's native `File` (from `node:buffer`) so test code matches real browser
// semantics for File reads.
import { File as NodeFile, Blob as NodeBlob } from 'node:buffer'

// jsdom installs its own File/Blob on globalThis; replace them with Node's
// spec-compliant versions which support `arrayBuffer()`, `text()`, and
// `stream()`.
;(globalThis as { File: unknown }).File = NodeFile
;(globalThis as { Blob: unknown }).Blob = NodeBlob
