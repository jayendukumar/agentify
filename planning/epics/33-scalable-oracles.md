# Epic 33 -- Scalable Oracles: Rules, Label-First Generation, Metamorphic Tests & Calibrated Judges

**Status: implemented (2026-10-11).** Groomed 2026-10-11 alongside Epic 32.
Built in `app/oracles/` (`rules.py` -- the expression language and
oracle; `service.py` -- rule proposals, oracle labelling and conflicts,
label-first generation, metamorphic variants, judge calibration, review
queue; `jobs.py`), `app/api/oracles.py`, and migration `d2e3f4a5b6c7`.
Frontend: the "Decision rules" and "Answers at scale" panels on the Golden
cases page. Variant *grading* (comparing base and variant decisions) lives
with batch results in Epic 35.

Goal: produce trustworthy expected results for thousands of cases without
a person labelling each one, by deriving them from things that are
already correct: the process's own business rules, labels chosen before
the data is generated, invariants that must hold whatever the answer is,
and (for free text only) a judge checked against human labels.

Depends on: Epic 32 (labelled datasets), Epic 25 (data generation), Epic
7 (blueprint and gateways). Feeds: Epics 34 and 35.

## Why

Expert labelling does not scale past a few hundred cases. Teams that test
at volume rely on ground truth that is correct by construction, ranked by
trust:

1. **Historical outcomes** (Epic 32, US32.3).
2. **Rule oracles.** Deterministic business rules compute the expected
   answer for any input.
3. **Label-first synthetic data.** Choose the answer, then generate an
   input that should produce it, then confirm it with the rule oracle.
4. **Metamorphic relations.** Check invariants instead of answers
   ("renaming the supplier must not change the decision").
5. **LLM-as-judge.** Used only for free-text outputs, and only trusted
   once its agreement with human labels has been measured.

This product has an advantage for item 2: the finalized BPMN already
contains the process's decision gateways. But gateway conditions are free
text today (see `GatewayDecision` in `app/schemas/orchestration.py` and
`GatewayBranch` in `app/schemas/twin_builder.py`), so they must be
structured before anything can execute them.

Epic 14 and Epic 19 chose structural grading over an LLM judge. This epic
keeps that principle: the judge (US33.6) is limited to fields that have no
structural signal, and it never overrides a rule-oracle or human label.

## User Stories

US33.1 -- Structured gateway rules.
As an Automation Architect, I want each gateway branch's free-text
condition proposed as a structured rule over named input fields (e.g.
`amount > 10000 AND vat_id IS MISSING -> escalate`), drafted by the LLM
from the condition text and confirmed or edited by me, with the original
text kept alongside, so the process's decision logic becomes executable.

US33.2 -- Rule oracle.
As an Automation Architect, I want the confirmed rules compiled into a
deterministic oracle that, for any dataset row, computes the expected
route, expected human-checkpoint decision, and any rule-determined output
fields, and fills them in as `oracle`-provenance labels, so thousands of
rows get labels without a person or an LLM call per row.

US33.3 -- Oracle conflict surfacing.
As a Process Owner, I want rows where the rule oracle disagrees with a
historical or expert label listed for me to resolve: either the label is
wrong, or the rules as documented do not match how the process really
runs. The second case is fed back as a gap to the blueprint (Epic 11), so
volume testing also tests the process documentation.

US33.4 -- Label-first generation.
As a Process Analyst, I want to set the outcome mix I need ("1,000 cases:
70% approve, 20% reject, 10% escalate; 15% messy") and have the Test Data
Kitchen generate inputs for each target label in batches. Each record is
confirmed by the rule oracle, and records the oracle disagrees with are
discarded or regenerated. The result is a large dataset whose labels are
correct by construction and whose class balance I control. Epic 25's
25-record cap still applies per generation call, not per dataset.

US33.5 -- Metamorphic relations.
As an Automation Architect, I want to declare relations on a scenario:
*invariance* (perturbing an irrelevant field such as name casing,
language, or field order must not change the decision) and
*directional* (raising `amount` past the approval limit must move the
decision toward escalate). The product generates the perturbed variants
(Epic 25's "make it tricky" mutations) and grades the agent on the
relation, so robustness is tested even where no ground truth exists.

US33.6 -- Calibrated LLM judge for free-text fields.
As an Automation Architect, I want to grade specific free-text output
fields (e.g. a rejection explanation) against a written rubric with an
LLM judge. The judge is first run on a human-labelled sample, and the
product shows its agreement rate with humans. It is only allowed into a
release gate once agreement passes a threshold I set, and it is
re-calibrated whenever the judge model or rubric changes.

US33.7 -- Disagreement-driven human review.
As a Process Owner, I want a review queue that contains only the rows
where the sources disagree (agent vs oracle vs judge vs historical label)
or the judge's confidence is low, sorted by impact, so expert time goes to
the few hundred cases that need it rather than the many thousands that
don't.

## Notes / Open Questions

- **Rule language.** Start with a small, typed expression language over
  `input_schema` fields (comparison, missing/present, AND/OR, membership)
  evaluated in Python. Avoid general code execution. Rules the language
  cannot express stay free text and contribute no oracle labels.
- An oracle only labels what the rules determine. Fields whose value
  depends on judgement (e.g. a free-text reason) remain unlabelled or
  go to US33.6.
- Rule structuring is useful beyond testing. Epic 16's orchestration
  rehearsal could resolve gateways automatically from the same rules
  instead of needing `gateway_decisions` per scenario. Out of scope here,
  but the rule model should not prevent it.
- Relationship to Epic 30 (compliance-aware testing): rules that come
  from organization policies (Epic 27's controls) should be linkable to
  the control they implement, so rule coverage and control coverage are
  the same number.
