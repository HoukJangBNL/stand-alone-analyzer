// web/src/pages/AdminPage.tsx
import { useState, useEffect } from 'react'
import { fetchUsage, updateUserRole, type UsageEvent } from '@/api/admin'
import type { UserRole } from '@/api/auth'

export function AdminPage() {
  const [usage, setUsage] = useState<UsageEvent[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [userId, setUserId] = useState('')
  const [role, setRole] = useState<UserRole>('member')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setLoading(true)
    fetchUsage({ limit: 100 })
      .then(setUsage)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load usage'))
      .finally(() => setLoading(false))
  }, [])

  const handleRoleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await updateUserRole(userId, role)
      alert(`User ${userId} role updated to ${role}`)
      setUserId('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update role')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div style={{ padding: 20 }}>
      <h2>Admin Panel</h2>

      <section style={{ marginBottom: 40 }}>
        <h3>Manage User Roles</h3>
        <form onSubmit={handleRoleSubmit} style={{ display: 'flex', gap: 8, alignItems: 'end' }}>
          <div>
            <label htmlFor="admin-user-id-input" style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>
              User ID
            </label>
            <input
              id="admin-user-id-input"
              data-testid="admin-user-id-input"
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              style={{ padding: 8 }}
            />
          </div>
          <div>
            <label htmlFor="admin-role-select" style={{ display: 'block', marginBottom: 4, fontSize: 12 }}>
              Role
            </label>
            <select
              id="admin-role-select"
              data-testid="admin-role-select"
              value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
              style={{ padding: 8 }}
            >
              <option value="member">member</option>
              <option value="reader">reader</option>
              <option value="operator">operator</option>
              <option value="admin">admin</option>
            </select>
          </div>
          <button
            data-testid="admin-role-submit"
            type="submit"
            disabled={submitting || !userId}
            style={{ padding: '8px 16px' }}
          >
            {submitting ? 'Updating...' : 'Update Role'}
          </button>
        </form>
      </section>

      <section>
        <h3>Usage Events</h3>
        {loading && <div>Loading usage...</div>}
        {error && <div style={{ color: '#b91c1c' }}>{error}</div>}
        {!loading && !error && usage.length === 0 && <div>No usage events.</div>}
        {!loading && usage.length > 0 && (
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #e5e7eb' }}>
                <th style={{ textAlign: 'left', padding: 8 }}>ID</th>
                <th style={{ textAlign: 'left', padding: 8 }}>User ID</th>
                <th style={{ textAlign: 'left', padding: 8 }}>Kind</th>
                <th style={{ textAlign: 'left', padding: 8 }}>Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {usage.map((evt) => (
                <tr
                  key={evt.id}
                  data-testid={`admin-usage-row-${evt.id}`}
                  style={{ borderBottom: '1px solid #f3f4f6' }}
                >
                  <td style={{ padding: 8, fontSize: 12 }}>{evt.id}</td>
                  <td style={{ padding: 8, fontSize: 12 }}>{evt.user_id}</td>
                  <td style={{ padding: 8, fontSize: 12 }}>{evt.kind}</td>
                  <td style={{ padding: 8, fontSize: 12 }}>{new Date(evt.ts).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
