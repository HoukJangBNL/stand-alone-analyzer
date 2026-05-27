// web/src/components/run/CascadeConfirmDialog.tsx
/**
 * P5.4 — CascadeConfirmDialog.
 *
 * Modal shown by ComputeTab when the user submits the pipeline form with a
 * dirty Background section. Background param changes invalidate downstream
 * artifacts (SAM, Domain Stats, Domain Proximity), so we surface the rerun
 * scope before the request leaves the client.
 *
 * Pure controlled component — parent owns the open/closed state via
 * conditional rendering and handles the start() call in onConfirm.
 */
interface Props {
  onCancel: () => void
  onConfirm: () => void
}

export function CascadeConfirmDialog({ onCancel, onConfirm }: Props) {
  return (
    <div
      data-testid="cascade-confirm-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="cascade-confirm-title"
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0, 0, 0, 0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        style={{
          background: '#fff',
          borderRadius: 6,
          padding: '20px 24px',
          maxWidth: 480,
          boxShadow: '0 6px 20px rgba(0, 0, 0, 0.2)',
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
        }}
      >
        <h3 id="cascade-confirm-title" style={{ margin: 0 }}>
          Cascade rerun
        </h3>
        <p style={{ margin: 0, color: '#333' }}>
          Background parameters changed. This will rerun: SAM, Domain Stats,
          Domain Proximity. Continue?
        </p>
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
            marginTop: 4,
          }}
        >
          <button
            type="button"
            data-testid="cascade-cancel"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="cascade-confirm"
            onClick={onConfirm}
          >
            Run with cascade
          </button>
        </div>
      </div>
    </div>
  )
}
