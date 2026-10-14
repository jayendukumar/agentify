import { useEffect, useState } from 'react'
import { ApiError, getOrganization, listContextEntities, listStarterPacks } from '../api/client'
import type { ContextEntity, Organization, StarterPackSummary } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import ApplicableContextView from '../components/org/ApplicableContextView'
import ApplicationCatalogPanel from '../components/org/ApplicationCatalogPanel'
import ConnectorsPanel from '../components/org/ConnectorsPanel'
import ContextEntityBrowser from '../components/org/ContextEntityBrowser'
import GovernancePanel from '../components/org/GovernancePanel'
import PolicyReviewPanel from '../components/org/PolicyReviewPanel'
import ProfileWizard from '../components/org/ProfileWizard'
import StarterPacksPanel from '../components/org/StarterPacksPanel'
import { handleTabKeyDown } from '../lib/tabKeyboard'

type Tab = 'profile' | 'policies' | 'applications' | 'context' | 'applies' | 'connectors' | 'governance'

const TABS: { id: Tab; label: string }[] = [
  { id: 'profile', label: 'Profile & rule packs' },
  { id: 'policies', label: 'Policy documents' },
  { id: 'applications', label: 'Applications & data' },
  { id: 'context', label: 'Browse context' },
  { id: 'applies', label: 'What applies where' },
  { id: 'connectors', label: 'Connectors' },
  { id: 'governance', label: 'Changes & access' },
]

/** Epic 27: the organization context the rest of the product draws on. */
export default function OrganizationPage() {
  const { can } = useAuth()
  const canEdit = can('context.edit')
  const canAdmin = can('org.admin')
  const [tab, setTab] = useState<Tab>('profile')
  const [organization, setOrganization] = useState<Organization | null>(null)
  const [packs, setPacks] = useState<StarterPackSummary[]>([])
  const [businessUnits, setBusinessUnits] = useState<ContextEntity[]>([])
  const [roles, setRoles] = useState<ContextEntity[]>([])
  const [error, setError] = useState<string | null>(null)
  const [refreshKey, setRefreshKey] = useState(0)

  async function refresh() {
    try {
      const [org, packList, units, roleList] = await Promise.all([
        getOrganization(),
        listStarterPacks(),
        listContextEntities('business_unit'),
        listContextEntities('role'),
      ])
      setOrganization(org)
      setPacks(packList)
      setBusinessUnits(units)
      setRoles(roleList)
      setRefreshKey((k) => k + 1)
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load the organization')
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  if (!organization) {
    return error ? (
      <div className="page"><p className="error" role="alert">{error}</p><button type="button" onClick={refresh}>Try again</button></div>
    ) : (
      <p className="loading-state" role="status">Loading organization...</p>
    )
  }

  return (
    <div className="page">
      <h1>{organization.name}</h1>
      <p className="meta">
        Your organization's units, countries, roles, policies, and rules. Every step that uses AI draws on the part of
        this that applies to the process in hand, and cites it.
      </p>
      {error && <p className="error" role="alert">{error}</p>}

      <div className="tab-bar" role="tablist" aria-label="Organization views" onKeyDown={handleTabKeyDown}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-org-${t.id}`}
            aria-controls={`panel-org-${t.id}`}
            aria-selected={tab === t.id}
            tabIndex={tab === t.id ? 0 : -1}
            className={`tab-button${tab === t.id ? ' tab-button-active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-org-${tab}`} aria-labelledby={`tab-org-${tab}`}>
        {tab === 'profile' && (
          <>
            <ProfileWizard organization={organization} packs={packs} canEdit={canEdit} onDone={() => refresh()} />
            <StarterPacksPanel packs={packs} canEdit={canEdit} onApplied={refresh} />
          </>
        )}
        {tab === 'policies' && <PolicyReviewPanel businessUnits={businessUnits} canEdit={canEdit} onChanged={refresh} />}
        {tab === 'applications' && <ApplicationCatalogPanel canEdit={can('catalog.edit')} onChanged={refresh} />}
        {tab === 'context' && (
          <ContextEntityBrowser businessUnits={businessUnits} roles={roles} canEdit={canEdit} refreshKey={refreshKey} onChanged={refresh} />
        )}
        {tab === 'applies' && <ApplicableContextView businessUnits={businessUnits} />}
        {tab === 'connectors' && <ConnectorsPanel businessUnits={businessUnits} canEdit={canAdmin} />}
        {tab === 'governance' && (
          <GovernancePanel organization={organization} businessUnits={businessUnits} canEdit={canAdmin} onChanged={refresh} />
        )}
      </div>
    </div>
  )
}
