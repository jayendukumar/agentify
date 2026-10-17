import { useCallback, useEffect, useState } from 'react'
import { ApiError, downloadTenantExport, listTenantExports, requestTenantExport } from '../api/client'
import type { TenantExport } from '../api/types'

// Epic 38, US38.13: an admin takes the organization's data with them --
// every table as JSON, the uploaded files and the audit log, in one zip
// file. A job makes it; it downloads once.

const STATUS_TEXT: Record<TenantExport['status'], string> = {
  queued: 'Waiting to start',
  running: 'Being made',
  ready: 'Ready to download',
  downloaded: 'Downloaded',
  failed: 'Failed',
  expired: 'Removed (not downloaded in time)',
}

// The API's timestamps are UTC without a zone.
function when(value: string | null): string {
  return value ? new Date(value.endsWith('Z') ? value : `${value}Z`).toLocaleString() : '--'
}

function size(bytes: number | null): string {
  if (bytes === null) return '--'
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function DataExportPage() {
  const [exports, setExports] = useState<TenantExport[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    listTenantExports()
      .then(setExports)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load exports'))
  }, [])
  useEffect(load, [load])

  // While one is being made, look again every few seconds.
  const pending = exports?.some((e) => e.status === 'queued' || e.status === 'running') ?? false
  useEffect(() => {
    if (!pending) return
    const timer = window.setInterval(load, 3000)
    return () => window.clearInterval(timer)
  }, [pending, load])

  async function start() {
    setError(null)
    setBusy(true)
    try {
      await requestTenantExport()
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not start the export')
    } finally {
      setBusy(false)
    }
  }

  async function download(item: TenantExport) {
    setError(null)
    try {
      const blob = await downloadTenantExport(item.id)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `axyntro-export-${item.id}.zip`
      link.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not download the export')
    } finally {
      load()
    }
  }

  return (
    <div className="page">
      <h1>Data export</h1>
      <p className="meta">
        Everything this organization holds -- every table as JSON, the uploaded files and the audit log -- in one zip
        file. It can be downloaded once, and is removed afterwards or if nobody downloads it in time. Sign-in secrets
        aren't in it: settings only hold references to them.
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="registry-entry-actions">
        <button type="button" onClick={start} disabled={busy || pending}>
          {pending ? 'Export in progress...' : 'Export all data'}
        </button>
      </div>
      {exports === null ? (
        <p className="loading-state" role="status">Loading...</p>
      ) : exports.length === 0 ? (
        <p className="meta">No exports yet.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr><th>Requested</th><th>By</th><th>Status</th><th>Contents</th><th /></tr>
          </thead>
          <tbody>
            {exports.map((item) => (
              <tr key={item.id}>
                <td>{when(item.created_at)}</td>
                <td>{item.requested_by_name}</td>
                <td>
                  {STATUS_TEXT[item.status]}
                  {item.status === 'downloaded' && item.downloaded_by_name && (
                    <span className="meta"> by {item.downloaded_by_name}, {when(item.downloaded_at)}</span>
                  )}
                  {item.status === 'ready' && item.expires_at && <span className="meta"> until {when(item.expires_at)}</span>}
                  {item.status === 'failed' && item.error && <span className="meta"> ({item.error})</span>}
                </td>
                <td>
                  {item.rows !== null ? `${item.rows.toLocaleString()} rows, ${item.file_count ?? 0} files, ${size(item.size_bytes)}` : '--'}
                </td>
                <td>
                  {item.status === 'ready' && (
                    <button type="button" className="button-secondary" onClick={() => download(item)}>Download (once)</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
