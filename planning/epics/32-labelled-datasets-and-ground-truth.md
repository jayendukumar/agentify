# Epic 32 -- Labelled Datasets & Ground Truth Management

**Status: groomed, not started.** Groomed 2026-10-11 from a discussion of
how the industry handles ground truth for agent evaluation and what volume
testing would need.

Goal: let one twin scenario be run against a whole dataset, where every
row carries its own inputs *and* its own expected results, and treat that
ground truth as a curated, versioned asset rather than values typed into
one scenario at a time.

Depends on: Epic 14 (`grade_run`), Epic 21 (environment `datasets`),
Epic 25 (Test Data Kitchen). Feeds: Epics 33, 34, 35.

## Why

Today a `TwinScenario` holds exactly one `inputs` dict and one
`expected_outputs`/`expected_steps` pair, so 500 cases means 500
hand-built scenarios. Industry practice for agent evaluation separates the
two concerns:

- **The test logic** (which systems are stubbed, how reviewers behave,
  what faults apply, how strictly steps must match) is written once.
- **The cases** live in a labelled dataset (a *golden set*): rows of
  inputs plus expected values, curated by subject-matter experts, split
  into typical / edge / adversarial segments, versioned like code, and
  grown from real failures.

Two further practices this epic adopts:

- **Partial labels.** Rows usually label only the fields that matter (the
  decision, the amount, the route), not the whole output. `grade_run`
  already compares key-by-key, so an unlabelled field is simply not graded.
- **Ambiguity is data, not a failure.** When experts disagree on the right
  answer, the case is marked ambiguous and kept out of the pass rate
  instead of silently counting against the agent.

## User Stories

US32.1 -- Labelled dataset model.
As an Automation Architect, I want a labelled dataset (named, versioned,
bound to an agent's `input_schema`) whose rows each hold `inputs` plus
optional `expected.*` labels (expected output fields, expected decision
at each human checkpoint, expected route), a segment tag, and a
provenance tag (`historical` / `synthetic` / `expert` / `oracle`), so
cases and test logic are stored separately.

US32.2 -- Data-driven scenarios.
As a Process Analyst, I want to point a scenario at a labelled dataset
instead of a single input, so the scenario's stubs, reviewer behaviour,
faults, and step-match mode are written once and graded per row against
that row's own labels. A scenario without a dataset keeps working exactly
as today.

US32.3 -- Historical-case import.
As a Process Analyst, I want to upload a CSV/XLSX of past cases exported
from the system of record, map its columns to the agent's input fields and
to expected-result labels (e.g. `final_status` -> `expected.decision`),
and have personal or identifying values pseudonymized on the way in (the
same treatment as Epic 25's clone & disguise), so real outcomes become
ground truth without retyping them.

US32.4 -- Label review and confirmation.
As a Process Owner, I want to page through rows as readable cards (Epic
25's US25.6), confirm or correct each label, and see who confirmed it and
when, so the golden set has an accountable owner rather than being
whatever the import or generator produced.

US32.5 -- Ambiguous cases.
As a Process Owner, I want to mark a row as ambiguous (with a note), and
optionally record a second reviewer's label so their agreement is
visible, so cases without one right answer are kept out of the pass rate
and flagged for a policy decision.

US32.6 -- Dataset versioning and diff.
As an Automation Architect, I want every change to a dataset's rows or
labels to create a new version, with a diff view (rows added, removed,
relabelled), and every run to record the dataset version it used (via
Epic 22 snapshots), so a pass-rate change can be traced to a data change
versus an agent change.

US32.7 -- Promote a failure to a golden case.
As an Automation Architect, I want to promote any failed or surprising
run, whether a single scenario, a dataset row, or an orchestration run, into a
labelled dataset row in one action, with the correct label filled in at
promotion time, so the golden set grows from real failures.

US32.8 -- Train/holdout split.
As an Automation Architect, I want a dataset partitioned into a visible
working set and a held-out set whose rows are not shown when editing the
agent definition, so improvements aren't tuned to the very cases used to
judge them.

## Notes / Open Questions

- **Storage.** Epic 21's `TwinEnvironment.datasets` is a free-form dict
  sized for a handful of records. A dataset of thousands of labelled rows
  needs its own table (one row per case) rather than a JSON blob on the
  environment. Environments keep referencing datasets by id and version.
- **Expected steps per row.** Most rows only need output and decision
  labels. Per-row `expected_steps` is allowed but optional; the
  scenario-level `expected_steps` acts as the default.
- Import pseudonymization sends values to the configured LLM provider
  only if LLM-based disguise is chosen. A deterministic, local
  field-level option (hash or fake-value substitution by column) should
  exist for data that must not leave the machine. See the decision log
  (2026-09-17) on provider choice.
