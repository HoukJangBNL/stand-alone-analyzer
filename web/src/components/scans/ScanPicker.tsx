// web/src/components/scans/ScanPicker.tsx
//
// Backwards-compat shim. The dropdown was replaced by a sortable table in
// W12 (docs/superpowers/plans/2026-05-26-W12-scan-table-and-delete.md).
// Keeping the export name avoids touching every tab page that imports it.
export { ScanTable as ScanPicker } from './ScanTable'
