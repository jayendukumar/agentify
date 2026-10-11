import type { ContextEntity, ContextKind } from '../api/types'

export const KIND_LABELS: Record<ContextKind, { singular: string; plural: string }> = {
  business_unit: { singular: 'Business unit', plural: 'Business units' },
  jurisdiction: { singular: 'Jurisdiction', plural: 'Jurisdictions' },
  role: { singular: 'Role', plural: 'Roles' },
  approval_authority: { singular: 'Approval authority', plural: 'Approval authorities' },
  regulation: { singular: 'Regulation', plural: 'Regulations' },
  obligation: { singular: 'Obligation', plural: 'Obligations' },
  control: { singular: 'Control', plural: 'Controls' },
  policy: { singular: 'Policy', plural: 'Policies' },
  glossary_term: { singular: 'Glossary term', plural: 'Glossary' },
  application: { singular: 'Application', plural: 'Applications' },
  data_class: { singular: 'Data class', plural: 'Data classes' },
  data_asset: { singular: 'Data asset', plural: 'Data assets' },
}

export const BROWSABLE_KINDS: ContextKind[] = [
  'business_unit',
  'jurisdiction',
  'role',
  'approval_authority',
  'regulation',
  'obligation',
  'control',
  'policy',
  'glossary_term',
  'application',
  'data_asset',
  'data_class',
]

/** One plain-language line describing an entity's kind-specific data. */
export function describeEntity(entity: ContextEntity): string {
  const d = entity.data as Record<string, unknown>
  switch (entity.kind) {
    case 'approval_authority': {
      const limit = d.limit_amount === null || d.limit_amount === undefined
        ? 'no limit'
        : `up to ${Number(d.limit_amount).toLocaleString()} ${String(d.currency ?? '')}`.trim()
      return `${String(d.role_name || 'Role')} may approve ${String(d.decision_type)} -- ${limit}`
    }
    case 'control': {
      const parts = [String(d.description ?? '')]
      if (d.requires_human_review) parts.push(`Needs human review${d.review_role ? ` by ${String(d.review_role)}` : ''}.`)
      return parts.filter(Boolean).join(' ')
    }
    case 'obligation':
      return String(d.text ?? '')
    case 'glossary_term': {
      const synonyms = (d.synonyms as string[] | undefined) ?? []
      return `${String(d.definition ?? '')}${synonyms.length ? ` (also: ${synonyms.join(', ')})` : ''}`
    }
    case 'data_asset': {
      const residency = (d.residency as string[] | undefined) ?? []
      return `${String(d.classification ?? 'Internal')}${residency.length ? ` · stored in ${residency.join(', ')}` : ''}`
    }
    case 'jurisdiction':
      return d.parent_code ? `Part of ${String(d.parent_code)}` : ''
    case 'application': {
      const aliases = (d.aliases as string[] | undefined) ?? []
      return aliases.length ? `Also known as: ${aliases.join(', ')}` : String(d.description ?? '')
    }
    default:
      return String(d.description ?? d.summary ?? '')
  }
}

export function describeScope(entity: ContextEntity, names: Record<string, string>): string {
  const parts: string[] = []
  const a = entity.applies_to
  if (a.business_units.length) parts.push(a.business_units.map((id) => names[id] ?? id).join(', '))
  if (a.jurisdictions.length) parts.push(a.jurisdictions.join(', '))
  if (a.data_classes.length) parts.push(`data: ${a.data_classes.join(', ')}`)
  if (a.process_types.length) parts.push(`process: ${a.process_types.join(', ')}`)
  if (a.systems.length) parts.push(`systems: ${a.systems.join(', ')}`)
  return parts.length ? parts.join(' · ') : 'Organization-wide'
}

export function sourceLabel(source: string): string {
  if (source.startsWith('starter_pack:')) return `Starter pack ${source.slice('starter_pack:'.length)}`
  if (source.startsWith('document:')) return 'Extracted from a policy document'
  if (source === 'profile_wizard') return 'Organization profile'
  if (source.startsWith('connector:')) return `Synced from ${source.slice('connector:'.length)}`
  if (source === 'import:csv') return 'Imported from CSV'
  return 'Added by hand'
}
