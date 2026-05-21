// web/src/components/explorer/DetailIdentity.tsx
interface Props {
  flakeId: string
  stem: string
  passes: boolean
}

export function DetailIdentity({ flakeId, stem, passes }: Props) {
  return (
    <div data-testid="detail-identity">
      <div><strong>flake_id:</strong> {flakeId}</div>
      <div><strong>stem:</strong> {stem}</div>
      <span
        data-testid="pass-chip"
        style={{
          display: 'inline-block', padding: '2px 6px', borderRadius: 4,
          background: passes ? '#1f6f3a' : '#7a1f1f', color: '#fff',
        }}
      >
        {passes ? 'PASS' : 'FAIL'}
      </span>
    </div>
  )
}
