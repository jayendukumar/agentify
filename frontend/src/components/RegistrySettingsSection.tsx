import { useEffect, useState, type FormEvent } from 'react'
import { ApiError, getRegistrySettings, saveRegistrySettings } from '../api/client'
import type { RegistrySetting, RegistrySettings } from '../api/types'

const SOURCE_TEXT: Record<RegistrySettings['source'], string> = {
  tenant: 'Set by your organization.',
  environment: "The deployment's default for your organization. Saving a list here replaces it.",
  default: 'The built-in registry. Saving a list here replaces it.',
}

// Epic 38, US38.11: which registries this organization publishes agents to
// and searches -- its own, not the deployment's.
export default function RegistrySettingsSection({ onSaved }: { onSaved: () => void }) {
  const [settings, setSettings] = useState<RegistrySettings | null>(null)
  const [list, setList] = useState<RegistrySetting[]>([])
  const [draft, setDraft] = useState<RegistrySetting>({ name: '', type: 'local' })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getRegistrySettings()
      .then((s) => {
        setSettings(s)
        setList(s.registries)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load the registry settings'))
  }, [])

  async function save(next: RegistrySetting[]) {
    setError(null)
    try {
      const saved = await saveRegistrySettings(next)
      setSettings(saved)
      setList(saved.registries)
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the registries')
    }
  }

  function add(event: FormEvent) {
    event.preventDefault()
    if (!draft.name.trim()) return
    void save([...list, { ...draft, name: draft.name.trim() }])
    setDraft({ name: '', type: draft.type })
  }

  if (settings === null) return error ? <p className="error" role="alert">{error}</p> : null
  return (
    <section aria-label="Manage registries">
      <h2>Your organization's registries</h2>
      <p className="meta">{SOURCE_TEXT[settings.source]}</p>
      <ul>
        {list.map((registry) => (
          <li key={registry.name}>
            {registry.name} <span className="meta">({registry.type})</span>{' '}
            <button type="button" className="button-secondary" onClick={() => save(list.filter((r) => r !== registry))}>
              Remove
            </button>
          </li>
        ))}
      </ul>
      <form className="identity-form" onSubmit={add} aria-label="Add a registry">
        <label>
          Registry name
          <input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="claims-team" />
        </label>
        <label>
          Type
          <select value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value })}>
            {settings.types.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <div className="registry-entry-actions"><button type="submit">Add registry</button></div>
      </form>
    </section>
  )
}
