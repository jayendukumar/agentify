import type { SessionInfo } from '../api/types'

// "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ... Chrome/130.0 Safari/537.36"
// -> "Chrome on Windows". Good enough to tell your laptop from your phone.
function describeBrowser(userAgent: string | null): string {
  if (!userAgent) return 'Unknown browser'
  const browser =
    [['Edg/', 'Edge'], ['Firefox/', 'Firefox'], ['Chrome/', 'Chrome'], ['Safari/', 'Safari']].find(([token]) =>
      userAgent.includes(token),
    )?.[1] ?? userAgent.split(/[\s/]/)[0]
  const os =
    [['Windows', 'Windows'], ['Android', 'Android'], ['iPhone', 'iOS'], ['iPad', 'iOS'], ['Mac OS', 'macOS'], ['Linux', 'Linux']].find(
      ([token]) => userAgent.includes(token),
    )?.[1]
  return os ? `${browser} on ${os}` : browser
}

function when(iso: string): string {
  // Naive UTC from the API.
  return new Date(iso.endsWith('Z') ? iso : `${iso}Z`).toLocaleString()
}

// Epic 37, US37.13: a user's live sign-ins, for their own Sessions page
// and the admin's Users page.
export default function SessionList({
  sessions,
  onEnd,
  endLabel = 'Revoke',
}: {
  sessions: SessionInfo[]
  onEnd: (session: SessionInfo) => void
  endLabel?: string
}) {
  if (sessions.length === 0) return <p className="meta">No active sessions.</p>
  return (
    <table className="org-register">
      <thead>
        <tr>
          <th>Browser</th>
          <th>IP address</th>
          <th>Signed in</th>
          <th>Last active</th>
          <th>Ends by</th>
          <th aria-label="Actions" />
        </tr>
      </thead>
      <tbody>
        {sessions.map((session) => (
          <tr key={session.id}>
            <td title={session.user_agent ?? undefined}>
              {describeBrowser(session.user_agent)}
              {session.current && <span className="badge role-badge"> This browser</span>}
            </td>
            <td>{session.ip ?? '-'}</td>
            <td>{when(session.created_at)}</td>
            <td>{when(session.last_used_at)}</td>
            <td>{when(session.expires_at)}</td>
            <td>
              {/* This browser signs out with "Log out" in the header. */}
              {!session.current && (
                <button type="button" className="button-secondary" onClick={() => onEnd(session)}>
                  {endLabel}
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
