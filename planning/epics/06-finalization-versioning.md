# Epic 6 -- Diagram Finalization and Versioning

Goal: Let users lock a reviewed as-is diagram as the accepted baseline, and
manage versions of it over time.

Depends on: Epic 4 (diagram UI), Epic 5 (chat editing).
Feeds: Epic 7 (blueprint generation requires a finalized baseline).

## User Stories

US6.1 -- Finalize action.
As a Process Analyst, I want to click "Finalize" to lock the current diagram
as the reviewed as-is baseline, so that downstream blueprint generation
works from a version I have explicitly approved.

US6.2 -- Version history.
As a Process Analyst, I want to see prior versions of the diagram and
restore an earlier one, so that finalizing is not a one-way, irreversible
action.

US6.3 -- Pre-finalize validation checks.
As a Process Analyst, I want the system to check for structural problems
(orphan nodes, unclosed flows, invalid BPMN) before allowing finalization,
so that I cannot lock in a broken diagram.

US6.4 -- Version comparison/diff view.
As a Process Analyst, I want to compare two versions of a diagram
side-by-side or as a highlighted diff, so that I can see exactly what
changed between them.

## Notes / Open Questions

A finalized version should be immutable; further chat/manual edits should
create a new draft version rather than mutate the finalized one, so blueprint
results (Epic 7) always trace back to a specific, unambiguous baseline.
