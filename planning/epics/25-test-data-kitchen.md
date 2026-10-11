# Epic 25 -- Test Data Kitchen (Sample & AI-Generated Data Sources)

**Status: all 7 stories implemented** (2026-10-10) -- `TestDataKitchen.tsx`
plus the `/test-builder/data` and `/test-builder/personas` endpoints.
Live-verified (clone & disguise replaced every identifier in a real
sample). The "chaos dial" stays deferred, as noted below.

Groomed 2026-10-09 alongside Epic 24.

Goal: give every twin test realistic input data without anyone typing
JSON -- ready-made sample packs per agent, plus on-the-fly LLM generation
driven by plain-language prompts -- stored as reusable, versioned
datasets and personas on a Digital Twin Environment (Epic 21, US21.4
already provides `datasets`/`personas` fields; nothing new to persist).

Depends on: Epic 21 (environments), Epic 12 (`input_schema`). Feeds:
Epic 24 step 3 ("Prepare data").

## Why "messy" matters

Published evaluations of synthetic personas/data find they are quick and
consistent but too clean -- missing the typos, gaps, and odd formats of
real data. The kitchen therefore makes mess a first-class, explicit
choice (a "Messy real-world" pack and "Make it tricky" mutations), rather
than hoping the generator produces it.

## User Stories

US25.1 -- Sample packs.
As a Process Analyst, I want one-click sample packs generated from the
agent's input fields -- *Typical*, *Edge cases* (boundary values, rare
but valid), and *Messy real-world* (typos, missing optional fields,
inconsistent formats) -- so I have data before I've thought about data.

US25.2 -- Describe-it generation.
As a Process Analyst, I want to describe the data I need in plain
language, with suggestion chips ("10 EU suppliers, 20% missing VAT IDs,
two duplicates"), and get schema-conformant records back, so I can tailor
data to my process without tooling.

US25.3 -- "Make it tricky" mutations.
As a Process Analyst, I want to pick one good record and generate
labelled variants of it -- boundary value, missing field, wrong format,
duplicate, different language -- so I can probe edge cases around one
known-good case.

US25.4 -- Clone & disguise.
As a Process Analyst, I want to paste a real example (CSV/JSON/text) and
have it turned into N look-alike synthetic records with any personal or
identifying data replaced, so tests have real structure without real
exposure. The original sample is never stored -- only the generated
records.

US25.5 -- Cast the people.
As a Process Owner, I want to generate reviewer personas ("Strict Sam,
Finance Manager -- rejects anything over $10k") that compile to a
human-checkpoint rule, so I choose reviewer behaviour by picking a
character rather than writing a rule.

US25.6 -- Records look like documents.
As a Process Analyst, I want records shown as readable cards (labelled
fields) rather than raw JSON, with the JSON one click away.

US25.7 -- Save to a reusable kit.
As an Automation Architect, I want generated datasets and personas saved
to a named environment ("<Agent> test kit"), versioned by Epic 21's
existing update-in-place mechanism, so other scenarios and agents can
reuse them.

## Notes / Open Questions

- One backend endpoint covers US25.1-25.4 (`POST .../twin/generate-data`
  with a `mode`), one covers US25.5 (`POST .../twin/generate-personas`) --
  both one structured-JSON LLM call, same shape as
  `app/twin/schema_inference.py`.
- Record count is capped server-side (25) to bound cost and latency.
- Clone & disguise (US25.4) sends the pasted sample to the configured LLM
  provider. The UI states that, since the provider choice (see
  decision-log 2026-09-17) matters for real data.
- A "chaos dial" (Calm / Realistic / Bad day) that sets fault
  probabilities across all systems is a natural follow-on but is
  deferred: per-situation faults (Epic 24, US24.3) cover the explicit
  cases first.
