import { useRef, useState } from 'react'
import { ApiError, importDatasetRows, previewDatasetImport } from '../../api/client'
import type { ColumnMapping, ColumnRole, ImportPreview, PseudonymMode } from '../../api/types'

const ROLES: { value: ColumnRole; label: string }[] = [
  { value: 'input', label: 'Agent input' },
  { value: 'label', label: 'Expected output' },
  { value: 'decision', label: 'Expected decision' },
  { value: 'segment', label: 'Segment' },
  { value: 'tag', label: 'Tag' },
  { value: 'ignore', label: 'Ignore' },
]

const PSEUDONYM: { value: PseudonymMode; label: string }[] = [
  { value: 'keep', label: 'Keep as is' },
  { value: 'fake', label: 'Replace with look-alike' },
  { value: 'hash', label: 'Replace with code' },
]

/** Epic 32, US32.3: import past cases from a CSV/XLSX export -- preview, a
 * column mapping you confirm, local pseudonymization, then import. */
export default function DatasetImportWizard({
  processId,
  datasetId,
  onImported,
}: {
  processId: string
  datasetId: string
  onImported: (message: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [mapping, setMapping] = useState<ColumnMapping[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function handlePreview() {
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setError('Choose a CSV or Excel file first.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await previewDatasetImport(processId, datasetId, file)
      setPreview(result)
      setMapping(result.suggested)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not read the file')
    } finally {
      setBusy(false)
    }
  }

  function update(index: number, patch: Partial<ColumnMapping>) {
    setMapping((current) => current.map((m, i) => (i === index ? { ...m, ...patch } : m)))
  }

  async function handleImport() {
    const file = fileRef.current?.files?.[0]
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const result = await importDatasetRows(processId, datasetId, file, mapping)
      setPreview(null)
      if (fileRef.current) fileRef.current.value = ''
      onImported(`Imported ${result.added} cases${result.skipped ? ` (${result.skipped} empty rows skipped)` : ''} -- now version ${result.version}.`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Import failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="org-upload-form">
      <fieldset disabled={busy}>
        <legend>Import past cases</legend>
        <p className="meta">
          An export from the system of record, with the outcome each case actually had. Personal details can be
          replaced here, on this machine, before anything is stored; the file itself is never kept.
        </p>
        <input ref={fileRef} type="file" accept=".csv,.xlsx" aria-label="Cases file" />
        <button type="button" className="button-secondary" onClick={handlePreview}>Preview</button>
      </fieldset>
      {error && <p className="error" role="alert">{error}</p>}
      {preview && (
        <div className="org-import-preview">
          <p className="meta">{preview.total_rows} rows found. Check what each column means:</p>
          <table>
            <thead>
              <tr><th>Column</th><th>Example</th><th>Means</th><th>Field</th><th>Personal data?</th></tr>
            </thead>
            <tbody>
              {mapping.map((m, index) => (
                <tr key={m.column}>
                  <td>{m.column}</td>
                  <td className="meta">{preview.sample[0]?.[m.column] ?? ''}</td>
                  <td>
                    <select aria-label={`Role of ${m.column}`} value={m.role} onChange={(e) => update(index, { role: e.target.value as ColumnRole })}>
                      {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                    </select>
                  </td>
                  <td>
                    {(m.role === 'input' || m.role === 'label') && (
                      <input aria-label={`Field for ${m.column}`} value={m.field ?? ''} onChange={(e) => update(index, { field: e.target.value || null })} />
                    )}
                  </td>
                  <td>
                    <select aria-label={`Pseudonymize ${m.column}`} value={m.pseudonymize} onChange={(e) => update(index, { pseudonymize: e.target.value as PseudonymMode })}>
                      {PSEUDONYM.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button type="button" className="button-primary" onClick={handleImport} disabled={busy}>
            {busy ? 'Importing...' : `Import ${preview.total_rows} cases`}
          </button>{' '}
          <button type="button" className="button-secondary" onClick={() => setPreview(null)}>Cancel</button>
        </div>
      )}
    </div>
  )
}
