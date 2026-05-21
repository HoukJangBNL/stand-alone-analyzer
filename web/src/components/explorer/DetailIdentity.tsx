// web/src/components/explorer/DetailIdentity.tsx
interface Props {
  flakeId: number
  imageId: number
  /**
   * Optional pass/fail chip. The detail Dto does not carry a `pass` field,
   * so callers can pass it through from the row context (or omit entirely).
   */
  passes?: boolean
}

export function DetailIdentity({ flakeId, imageId, passes }: Props) {
  return (
    <div data-testid="detail-identity">
      <div><strong>flake_id:</strong> {flakeId}</div>
      <div><strong>image_id:</strong> {imageId}</div>
      {passes !== undefined && (
        <span
          data-testid="pass-chip"
          style={{
            display: 'inline-block', padding: '2px 6px', borderRadius: 4,
            background: passes ? '#1f6f3a' : '#7a1f1f', color: '#fff',
          }}
        >
          {passes ? 'PASS' : 'FAIL'}
        </span>
      )}
    </div>
  )
}
