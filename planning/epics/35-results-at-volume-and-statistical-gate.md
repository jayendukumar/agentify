# Epic 35 -- Results at Volume & Statistical Release Gate

**Status: implemented (2026-10-11).** Groomed 2026-10-11 alongside Epic 32.
Built in `app/batches/results.py` (Wilson intervals, segments, decision
matrix, failure clusters, consistency, distributions, relation grading,
load profile, McNemar comparison, gate, evidence), `app/api/results.py`,
the gate check in mark-release-candidate, and migration `f4a5b6c7d8e9`.
Frontend: `BatchResultsPage`.

Goal: turn thousands of graded rows into a few trustworthy, readable
numbers and the handful of failure patterns behind them, and let the
release gate decide on those numbers with known confidence rather than on
"10/11 passed".

Depends on: Epic 34 (batch runs), Epic 32 (segments, ambiguity), Epic 19
(evaluation dimensions, version comparison), Epic 23 (release gate),
Epic 26 (business-readable results). Feeds: Epic 30 (compliance evidence).

## Why

Epic 19's suite result ("10/11 Passed") and Epic 26's per-run stories
are the right views for a dozen scenarios. At 1,000 rows they become a
wall of results nobody reads, and a single pass rate hides the important
questions:

- **How sure are we?** 95% on 40 rows and 95% on 400 rows mean different
  things. Roughly 385 rows are needed to know a pass rate within ±5% at
  95% confidence.
- **Where does it fail?** An agent at 96% overall can be at 60% on
  messy-data cases.
- **Is the new version actually better?** Two pass rates from different
  batches can differ by noise alone. A paired comparison on the same rows
  (McNemar's test) answers whether the change is real.
- **Is it consistent?** A case that passes 2 of 3 times is a different
  risk from one that always fails.

## User Stories

US35.1 -- Pass rate with confidence.
As a Process Owner, I want a batch's headline result shown as a pass rate
with a confidence range ("94% ± 2%, 1,000 cases") and a plain-language
reading of it, with ambiguous rows (US32.5) excluded and counted
separately, so I know how much to trust the number.

US35.2 -- Segment breakdown.
As a Process Owner, I want pass rate broken down by dataset segment
(typical / edge / messy / custom tags) and by label provenance
(historical / synthetic / oracle / expert), with weak segments called
out, so a strong average can't hide a weak area.

US35.3 -- Decision matrix and per-field accuracy.
As an Automation Architect, I want a matrix of expected vs actual
decisions (approve / reject / escalate) and an accuracy figure per
labelled output field, so I can see *how* the agent is wrong, e.g.
approves what it should escalate, which matters more than the reverse.

US35.4 -- Failure clustering.
As an Automation Architect, I want failed rows grouped by deviation
pattern ("34 cases: skipped the VAT lookup"; "21 cases: approved over the
$10k limit"), each with example rows and an Epic 26 "fix this" link, so
1,000 results reduce to a handful of problems to work on.

US35.5 -- Consistency (flakiness) view.
As an Automation Architect, I want rows run with multiple trials
classified as always-pass, always-fail, or inconsistent, with
pass^k shown next to the pass rate, so inconsistent behaviour is visible
as its own risk.

US35.6 -- Paired version comparison at volume.
As an Automation Architect, I want to compare two agent versions on the
same dataset version: rows that got fixed, rows that regressed, net
change, and whether the difference is statistically significant (paired
test), extending Epic 19's US19.4 from two runs to two batches, so
"v1.3 is better than v1.2" is a measured statement.

US35.7 -- Statistical release gate.
As a Process Owner, I want to set release-gate criteria in terms of the
regression tier (Epic 34, US34.8): a minimum pass rate measured by the
*lower* end of its confidence range, minimum per-segment rates, zero
failures on rows tagged critical, and no significant regression against
the currently approved version. Epic 23's `EVALUATED` to
`RELEASE_CANDIDATE` step then checks these instead of a raw count.

US35.8 -- Cost, latency, and efficiency distribution.
As a Platform Engineer, I want token, cost, turn, and latency figures
for a batch shown as distributions (median, p95, worst rows) rather than
totals, along with the load-profile results from US34.10, so expensive or
slow outliers are visible.

US35.9 -- Exportable evidence.
As a Compliance Lead, I want a batch's results (dataset version, agent
version, snapshot ids, headline figures with confidence, segment
breakdown, gate decision) exported as a single evidence record, so volume
test results can support Epic 30's compliance evidence export.

## Notes / Open Questions

- **Confidence method.** Use Wilson intervals for pass rates (they behave
  well near 0% and 100%, unlike the simple normal approximation) and
  McNemar's test for paired version comparison. Show the method in a
  tooltip, not in the headline.
- **Plain language first.** Following Epic 26, the default view is
  written for a Process Owner ("The agent handles about 94 in 100 cases
  correctly; it struggles with messy supplier data"). The matrix,
  intervals, and test statistics sit one level down for the Automation
  Architect.
- Failure clustering (US35.4) can start deterministically by grouping on
  `TwinDeviation.reason` templates and the failing field or step. An
  LLM-written cluster summary is optional polish, not the mechanism.
