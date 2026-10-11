# Epic 30 -- Compliance-Aware Testing & Release

**Status: implemented (2026-10-11).** Groomed 2026-10-11 from
[enterprise-context-strategy.md](../enterprise-context-strategy.md).
Built in `app/twin/compliance.py` (assertion grading, wired into
`grade_run`), `app/twin/test_builder.py` (rule coverage targets, rule-driven
suggestions, approval-matrix reviewers, jurisdiction formats, near-miss
data), `app/context/compliance.py` (evidence, the release gate, Markdown
export), and the agents/test-builder APIs. Frontend: the Rules line on the
coverage meter, rule checks on situation cards, "Use our approval limits"
and "Near misses" in the data kitchen, and the compliance evidence panel
in the lifecycle section.

Goal: make the digital twin test the organization's *rules*, not just its
paths and failures, and make the release gate (Epic 23) require evidence
that every applicable control was exercised. The output is a per-agent
**compliance evidence** record a compliance team can review.

Depends on: Epic 24-26 (guided test builder), Epic 27 (controls, approval
authority), Epic 28 (resolver, pinned packs), Epic 29 (application data
formats). Extends: Epic 23 (release gate).

## User Stories

US30.1 -- Control coverage targets.
As a Process Analyst, I want the test builder's coverage meter to include
a **Rules** category, with one target per applicable control (e.g.
"potential sanctions match is escalated", "missing consent blocks
onboarding"), so untested rules are as visible as untested paths.

US30.2 -- Rule-driven situation suggestions.
As a Process Analyst, I want suggested situations to include cases designed
to trigger each applicable control, citing the control. Their expectations
should reflect what the control demands: escalation to the named role, a
blocked action, or a required log entry.

US30.3 -- Reviewers from the approval-authority matrix.
As a Process Owner, I want reviewer personas derived automatically from the
approval-authority matrix (e.g. "Head of Operations -- rejects above
€50,000"), so threshold tests use the organization's real limits rather
than invented ones. This extends Epic 25's reviewer casting.

US30.4 -- Realistic, format-correct test data.
As a Process Analyst, I want the data kitchen to generate records in the
formats of the process's jurisdictions and data assets (e.g. IBANs, national
ID shapes, date formats), and to include near-miss records for screening
controls, while still never using real personal data.

US30.5 -- Compliance assertions in grading.
As an Automation Architect, I want a situation to assert control
behaviour, not only tool calls: "escalated to role X", "action blocked",
"decision logged with explanation". These grade alongside the existing
`in_order` steps.

US30.6 -- Control coverage in the release gate.
As a Compliance Lead, I want Epic 23's checklist to gain "every applicable
control has at least one passing situation on the current agent version
and pinned context pack", so an agent cannot reach RELEASE_CANDIDATE with
an untested rule.

US30.7 -- Compliance evidence view and export.
As a Compliance Lead, I want a per-agent view mapping each applicable
control to its passing situations, run dates, agent version, and pinned
policy versions, exportable as a document, so evidence can go straight into
an audit file.

## Notes / Open Questions

- **Decision support, not certification.** Evidence shows that the
  simulated agent behaved as the rule requires in the tested situations.
  It is not a guarantee of compliance. The UI and the export must say so.
- When the context pack changes (Epic 28, US28.8), previously passing
  control evidence is marked out-of-date rather than deleted.
- Which controls are testable in a twin at all (vs. purely organizational
  controls like "annual training") needs a `testable` flag on the control,
  set at confirmation time in Epic 27.
