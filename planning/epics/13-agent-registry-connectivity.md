# Epic 13 -- Agent Registry Connectivity

Goal: Connect the product to one or more agent registries so generated
artifacts have somewhere real to be published to and looked up from, without
assuming a specific vendor or standard up front.

Depends on: Epic 9 (config/secrets handling for registry credentials, US9.10).
Feeds: Epic 15 (publishing needs a connected registry).

This epic is independent of Epic 12 and Epic 14 -- connecting to a registry
doesn't require an agent artifact to exist yet, and can be built in parallel
with them.

## User Stories

US13.1 -- Pluggable registry connector interface.
As a Platform Engineer, I want a registry connector defined behind a common
interface (push, pull/lookup, search, auth check), so that adding or
swapping a registry backend later doesn't touch the rest of the product --
the target registry product/standard is explicitly undecided (see
`planning/backlog.md`'s original open question on this).

US13.2 -- Local, built-in reference registry.
As a Platform Engineer, I want a simple, local, database-backed connector
implementing that interface as the default, so that publish/browse/search
flows can be built and demonstrated end-to-end today, without blocking on a
specific vendor registry being chosen or available.

US13.3 -- Registry connection configuration.
As a Platform Engineer, I want to configure a registry connection (endpoint,
credentials) via the existing environment/config convention, so that
registry credentials are never hardcoded or committed to source control.

US13.4 -- Multiple simultaneous registries.
As an Automation Architect, I want to configure more than one registry
connection at a time (for example an internal one and a vendor one), so
agents can be published to the right destination per team or policy without
the product forcing one global choice.

US13.5 -- Browse and search registered agents.
As an Automation Architect, I want to browse and search agents already
registered across connected registries, so I can check for an existing
equivalent agent before generating and publishing a duplicate for the same
process step.

US13.6 -- Connection health and error surfacing.
As a Platform Engineer, I want registry-unreachable and auth-failure states
surfaced clearly in the UI, so I know immediately whether something failed
because of the registry connection itself, as distinct from a failure on a
specific publish attempt (Epic 15).

## Notes / Open Questions

The original backlog's open question -- "which registry product/standard is
the target?" -- is resolved here by deliberately *not* resolving it yet:
US13.1/US13.2 ship a working local reference implementation first, so Epic
15 (publishing) can be built and demoed without blocking on a vendor
decision. Adding a real vendor connector later is additive work behind the
same interface, not a rework of the publish flow.

US13.5's dedup/search is only exact against the local reference registry
(it's our own schema). Against a real external registry it's realistically a
best-effort name/tag match, not a guaranteed dedup -- the UI should say so
rather than imply an exact match it can't back up.
