---
name: decision-log
description: Use immediately after fixing a defect that only surfaced by actually running/testing code against something real (a live API, a live database, a live browser) -- not one caught by reading code or an obvious typo -- or after making a design/architecture decision where more than one reasonable option existed and one was chosen for a specific reason. Append an entry to planning/decision-log.md capturing the reasoning, in the Agentic Solution Generator project. Also use when the user asks to log, record, or write up a decision, finding, or defect.
---

# Decision Log Maintenance

This skill keeps `planning/decision-log.md` current: a running record of
*why*, not *what*, for this project. The epics (`planning/epics/`) and the
code already track what got built and its current status; this file is
for the judgment calls and defects that would otherwise only live in a
chat transcript nobody re-reads.

Read the existing `planning/decision-log.md` first (if it exists) to match
its voice and structure before appending -- don't design a new format each
time.

## When an entry is warranted

Write an entry when **any** of these happened:

- **A defect found only by running the code against something real**
  (a live LLM API call, a live database, a live browser, a real file) --
  not one visible from reading the code, not one a passing mocked test
  already covered before the fix. The interesting part is usually *why*
  it was invisible until that point (what earlier assumption did the real
  system violate?).
- **A design/architecture decision with a genuine trade-off** -- more than
  one reasonable approach existed, and one was chosen for a stated reason
  (cost, simplicity, matching an existing pattern, a scope cut, etc.).
  This includes reversing an earlier instinct once testing or review
  showed it was wrong (e.g. "first tried X, a test exposed why that was
  wrong, redesigned as Y").
- **Reality contradicted an assumption** made earlier (by a human or by a
  prior session) -- an API behaves differently than documented, a library
  version shipped a breaking change, a "should be fine" turned out not to
  be.

Do **not** write an entry for: routine CRUD/plumbing with no real
judgment call, a typo or obvious off-by-one fix, a decision already fully
explained by an existing planning doc (link to it instead of restating
it), or work that's still in progress (log the finished decision, not the
exploration).

When in doubt, prefer logging: a slightly-too-generous log is easy to skim
past; a missing entry is a decision nobody can recover later.

## Format

Append under the current session's date header (create one if this is the
first entry of a new working session -- match the existing
`## YYYY-MM-DD [to YYYY-MM-DD] -- <short theme>` style). Under it, one
`###`-level entry per decision or defect, following the tone already in
the file:

- **Lead with the decision or the bug**, not a narrative of how it was
  found, unless the finding process itself is the interesting part.
- **State the *why*** -- the reasoning, the alternative(s) considered and
  rejected, or (for a defect) the root cause and why it was invisible
  until that point.
- **Keep it short.** A few sentences to a short paragraph per entry. This
  is a log, not a postmortem report -- link to a planning doc, a skill, or
  a specific file/module for the full detail rather than duplicating it.
- Name specific files/modules where it helps a future reader jump straight
  to the relevant code, but don't paste large code blocks or diffs in.

## When to do this

Write the entry at the point the decision is made or the defect is fixed
and verified -- not saved up for an end-of-session summary. A long session
compacts context as it goes, and reasoning that felt sharp in the moment
is exactly what gets lost first when reconstructed later from a summary.
If several qualifying things happen in one session, that's several
entries under one date header, added as they happen.

## Housekeeping

If `planning/decision-log.md` doesn't exist yet, create it with the header
block from the current version (context, what earns an entry, pointer to
this skill) before adding the first entry.

If the file grows large enough that skimming it becomes unwieldy (a rough
guide: well past a year of active entries, or it's clearly slowing down
reads), propose splitting into per-year or per-quarter files
(`planning/decision-log-2027.md`, etc.) with `decision-log.md` becoming an
index -- but don't do this preemptively; a single growing file is the
right default for now.
