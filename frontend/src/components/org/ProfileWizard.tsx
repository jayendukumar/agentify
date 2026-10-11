import { useState } from 'react'
import { ApiError, applyOrganizationProfile } from '../../api/client'
import type { ApprovalRuleAnswer, Organization, ProfileResult, StarterPackSummary } from '../../api/types'

interface Props {
  organization: Organization
  packs: StarterPackSummary[]
  canEdit: boolean
  onDone: (result: ProfileResult) => void
}

function splitList(value: string): string[] {
  return value.split(',').map((v) => v.trim()).filter(Boolean)
}

/** US27.3: a short questionnaire that seeds the organization context. */
export default function ProfileWizard({ organization, packs, canEdit, onDone }: Props) {
  const previous = (organization.profile.answers ?? {}) as Partial<Record<string, unknown>>
  const [name, setName] = useState(organization.name === 'My organization' ? '' : organization.name)
  const [industry, setIndustry] = useState(organization.industry ?? '')
  const [size, setSize] = useState(organization.size ?? '')
  const [countries, setCountries] = useState(((previous.countries as string[]) ?? []).join(', '))
  const [units, setUnits] = useState(((previous.business_units as string[]) ?? []).join(', '))
  const [applications, setApplications] = useState(((previous.applications as string[]) ?? []).join(', '))
  const [rules, setRules] = useState<ApprovalRuleAnswer[]>(
    (previous.approval_rules as ApprovalRuleAnswer[]) ?? [{ role: '', decision_type: '', limit_amount: null, currency: '' }],
  )
  const [selectedPacks, setSelectedPacks] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ProfileResult | null>(null)

  function updateRule(index: number, patch: Partial<ApprovalRuleAnswer>) {
    setRules((current) => current.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)))
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!name.trim()) {
      setError('Enter your organization name.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const saved = await applyOrganizationProfile({
        name: name.trim(),
        industry: industry.trim() || null,
        size: size || null,
        countries: splitList(countries),
        business_units: splitList(units),
        applications: splitList(applications),
        approval_rules: rules.filter((r) => r.role.trim() && r.decision_type.trim()),
        apply_packs: selectedPacks,
      })
      setResult(saved)
      onDone(saved)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save the profile')
    } finally {
      setSaving(false)
    }
  }

  const createdSummary = result
    ? Object.entries(result.created).map(([kind, count]) => `${count} ${kind.replace('_', ' ')}${count === 1 ? '' : 's'}`).join(', ')
    : ''

  return (
    <form className="org-wizard" onSubmit={handleSubmit} aria-label="Organization profile">
      <p className="meta">
        A few answers give every later step useful context: which rules apply, who approves what, and which systems you
        use. You can re-run this at any time; it adds to and corrects what is there rather than duplicating it.
      </p>
      <fieldset disabled={!canEdit || saving}>
        <legend>About your organization</legend>
        <label>Organization name<input value={name} onChange={(e) => setName(e.target.value)} required /></label>
        <label>Industry<input value={industry} onChange={(e) => setIndustry(e.target.value)} placeholder="e.g. Retail banking" /></label>
        <label>
          Size
          <select value={size} onChange={(e) => setSize(e.target.value)}>
            <option value="">Choose...</option>
            <option value="small">Fewer than 200 people</option>
            <option value="mid">200 to 5,000 people</option>
            <option value="large">More than 5,000 people</option>
          </select>
        </label>
        <label>
          Countries you operate in
          <input value={countries} onChange={(e) => setCountries(e.target.value)} placeholder="e.g. DE, FR, UK, US-CA" aria-describedby="countries-help" />
        </label>
        <p id="countries-help" className="meta">Two-letter country codes, comma separated. US states as US-CA, US-NY.</p>
        <label>
          Business units
          <input value={units} onChange={(e) => setUnits(e.target.value)} placeholder="Leave empty if you are one business" />
        </label>
        <label>
          Main applications
          <input value={applications} onChange={(e) => setApplications(e.target.value)} placeholder="e.g. Salesforce, SAP, Core banking" />
        </label>
      </fieldset>

      <fieldset disabled={!canEdit || saving}>
        <legend>Who approves what</legend>
        <p className="meta">For example: the Head of Operations approves refunds up to 5,000 EUR.</p>
        {rules.map((rule, index) => (
          <div className="org-rule-row" key={index}>
            <label>Role<input value={rule.role} onChange={(e) => updateRule(index, { role: e.target.value })} placeholder="Head of Operations" /></label>
            <label>Decision<input value={rule.decision_type} onChange={(e) => updateRule(index, { decision_type: e.target.value })} placeholder="refund" /></label>
            <label>
              Up to
              <input
                type="number"
                min={0}
                value={rule.limit_amount ?? ''}
                onChange={(e) => updateRule(index, { limit_amount: e.target.value === '' ? null : Number(e.target.value) })}
                placeholder="no limit"
              />
            </label>
            <label>Currency<input value={rule.currency} onChange={(e) => updateRule(index, { currency: e.target.value.toUpperCase() })} placeholder="EUR" maxLength={3} /></label>
            <button type="button" className="button-secondary" onClick={() => setRules((r) => r.filter((_, i) => i !== index))} aria-label={`Remove approval rule ${index + 1}`}>
              Remove
            </button>
          </div>
        ))}
        <button type="button" className="button-secondary" onClick={() => setRules((r) => [...r, { role: '', decision_type: '', limit_amount: null, currency: '' }])}>
          Add another
        </button>
      </fieldset>

      {packs.length > 0 && (
        <fieldset disabled={!canEdit || saving}>
          <legend>Starter rule packs</legend>
          <p className="meta">Curated, plain-language rules you can add now. Decision support only, not legal advice.</p>
          {packs.map((pack) => (
            <label className="check-row" key={pack.id}>
              <input
                type="checkbox"
                checked={selectedPacks.includes(pack.id)}
                onChange={(e) => setSelectedPacks((s) => (e.target.checked ? [...s, pack.id] : s.filter((id) => id !== pack.id)))}
              />
              {pack.title}
              {pack.applied_version && <span className="badge status-done">added v{pack.applied_version}</span>}
            </label>
          ))}
        </fieldset>
      )}

      {error && <p className="error" role="alert">{error}</p>}
      {result && (
        <p className="info" role="status">
          Saved.{createdSummary ? ` Added ${createdSummary}.` : ' Nothing new to add.'}
          {result.suggested_packs.length > 0 && ` Suggested rule packs for your countries: ${result.suggested_packs.join(', ')}.`}
        </p>
      )}
      <button type="submit" className="button-primary" disabled={!canEdit || saving}>
        {saving ? 'Saving...' : 'Save profile'}
      </button>
      {!canEdit && <p className="meta">Editor access is required to change the organization profile.</p>}
    </form>
  )
}
