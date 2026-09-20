# Epic 15 -- Agent Publishing & Lifecycle Management

Goal: Publish a generated -- and ideally twin-validated -- agent artifact to
a connected registry, and track its status over its lifecycle.

Depends on: Epic 12 (an artifact to publish), Epic 13 (a registry to publish
to). Soft-depends on Epic 14 (twin results are surfaced at publish time, but
passing is not required to unlock publishing -- see Notes).

## User Stories

US15.1 -- Publish action per artifact.
As an Automation Architect, I want a "Publish" action on a generated agent
artifact that pushes it to a chosen connected registry, so the agent becomes
discoverable and usable outside this product.

US15.2 -- Publish status tracking.
As an Automation Architect, I want to see each agent's status (draft ->
generated -> published -> deployed) per blueprint node, so I always know how
far along an agent is without checking the registry directly.

US15.3 -- Twin-result visibility at publish time.
As an Automation Architect, I want to see the latest digital-twin results
(if any exist) right on the publish action, so I can make an informed
publish decision even though passing isn't a hard, system-enforced gate.

US15.4 -- Republish on regeneration, as a new version.
As an Automation Architect, I want to republish as a new version -- not
silently overwrite -- when the underlying artifact was regenerated after
being marked stale (Epic 12's US12.4), so the registry's history reflects
real change over time instead of mutating a previously-published record.

US15.5 -- Publish failure handling.
As an Automation Architect, I want a failed publish (registry unreachable,
auth failure, conflict) to leave the artifact's local status unchanged and
show a clear, retryable error, so a network blip is never misreported as
"published," and the artifact's draft state is never lost.

US15.6 -- Access control on publish.
As a Platform Engineer, I want publishing restricted to editor-role users
(the same tier as finalize), so that pushing an agent to a shared registry
is always an intentional, attributable action rather than something a
viewer can trigger.

## Notes / Open Questions

US15.6 deliberately reuses the existing viewer/editor role model (Epic 9/10's
US9.9/US10.4) rather than introducing a new role tier -- publishing to a
shared registry is a bigger blast radius than editing a diagram, but not
different enough yet to justify a third role. Revisit if a real registry
integration turns out to need its own separate approval step.

"Deployed," the fourth lifecycle status, is tracked here as a status field
only -- this epic does not build actual agent deployment/runtime hosting.
Reaching "deployed" is either a manual action (a user marking it once
they've deployed it elsewhere) or, if a chosen registry's API reports
deployment state, synced from that. A real deployment target is out of
scope for Phase 2 as scoped in the original backlog.

Twin results are advisory, not a hard gate (US15.3), because Epic 14 is
explicitly the most speculative, discovery-needed part of this phase --
hard-gating publish on it would block the whole publishing flow on work that
may take longer to land. This can be tightened into a real gate later once
Epic 14 has enough real usage to trust its signal.
