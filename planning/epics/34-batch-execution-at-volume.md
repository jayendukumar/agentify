# Epic 34 -- Batch Execution at Volume

**Status: implemented (2026-10-11).** Groomed 2026-10-11 alongside Epic 32.
Built in `app/batches/service.py` (sampling, estimates, start/control,
budget gate, trace retention, replay, schedules), the `twin_run` and
`load_batch` job handlers in `app/connectors/jobs.py`, per-run seeds and
simulated latency in the twin executor/engine, throttling and cache
marking in `app/llm/client.py`, `app/api/batches.py`, and migration
`e3f4a5b6c7d8`. Frontend: the "Run at volume" panel on the Golden cases
page. Provider batch APIs (Notes) are not used, as the epic anticipated.

Goal: run a scenario against hundreds or thousands of dataset rows,
several times each, reliably, within a known budget, and reproducibly,
without blocking the API or the user's session.

Depends on: Epic 32 (data-driven scenarios), Epic 22 (snapshots), Epic
21 (fault injection), Epic 19 (regression suites). Shares US31.1 (job
queue) with Epic 31 and pulls it forward. Feeds: Epic 35.

## Why

Twin runs and suite runs are dispatched with FastAPI `BackgroundTasks`
today (e.g. `app/api/evaluation.py`). That works for a suite of 11
scenarios. It does not work for 1,000 rows × 3 trials: there are no
retries, it does not survive a restart, there is no concurrency limit
against the LLM provider's rate limits, and a run cannot be resumed.

Agents are also non-deterministic, so one run per case does not give a
reliable signal. Industry practice is to run each case k times and
report both "passed at least once" (pass@k) and "passed every time"
(pass^k, from τ-bench). The second measures the consistency a business
process needs.

The twin's simulated systems are cheap. Cost and time are dominated by
the agent's LLM calls, so budgeting and caching matter more than raw
compute.

## User Stories

US34.1 -- Durable job queue for runs.
As a Platform Engineer, I want twin and suite runs executed by the job
queue from Epic 31's US31.1 (retries, concurrency limits, survives
restarts) instead of `BackgroundTasks`, with one job per dataset row ×
trial, so a large batch is resilient and a failed row is retried on its
own. This story pulls US31.1 forward. Whichever epic is built first
owns it.

US34.2 -- Batch run with repeats.
As an Automation Architect, I want to start a batch run of a data-driven
scenario or suite with a chosen number of trials per row (default 1, up
to e.g. 5), so flaky cases show up as cases that pass inconsistently
rather than as random noise between runs.

US34.3 -- Cost and time estimate before starting.
As a Process Owner, I want an estimate before a batch starts (rows ×
trials × average tokens and latency per run from recent runs of this
agent) and the option to set a hard budget cap that stops the batch when
reached, so a large test cannot produce a surprise bill.

US34.4 -- Provider-aware throttling.
As a Platform Engineer, I want batch concurrency bounded per LLM
provider and model, with backoff on rate-limit responses (distinct from
Epic 21's *simulated* 429 faults, which hit the agent's tools, not the
agent's own model calls), so a batch runs as fast as the provider allows
without failing en masse.

US34.5 -- Prompt caching across a batch.
As a Platform Engineer, I want the parts of the agent's prompt that are
identical across every row (system prompt, tool definitions, knowledge
context) sent in a cacheable form where the provider supports it, so
per-row cost reflects only what changes per row.

US34.6 -- Seeded, replayable rows.
As an Automation Architect, I want each row × trial to record its
fault-injection seed (`roll_fault`/`roll_faults` in `app/twin/
environments.py`) and its snapshot (Epic 22), and any single row to be
re-run alone with the same seed, so one failure out of 1,000 can be
reproduced and debugged without re-running the batch.

US34.7 -- Pause, resume, cancel, and partial results.
As an Automation Architect, I want to pause, resume, or cancel a batch
and see results stream in as rows complete, so a batch that is clearly
failing can be stopped early and its partial results still used.

US34.8 -- Sampling and tiered suites.
As an Automation Architect, I want to run a stratified random sample of a
dataset (e.g. 10% per segment) and to define tiers on a suite: *smoke*
(~10 rows, on every regeneration), *regression* (a few hundred, the
release-gate tier), and *full* (everything, scheduled), so volume testing
is matched to the decision being made.

US34.9 -- Scheduled full runs.
As an Automation Architect, I want the full tier run on a schedule
(e.g. nightly, or when a dataset or context pack changes per Epic 28's
staleness propagation), so drift is caught without someone remembering
to start it.

US34.10 -- Throughput and latency profile.
As a Platform Engineer, I want a separate *load profile* mode that runs a
batch at a target concurrency, with simulated system latency on the
stubs, and reports p50/p95/p99 end-to-end latency and failures under load
apart from correctness results, so the question "does it hold up at
volume?" isn't mixed up with "is it right at volume?".

## Notes / Open Questions

- **Provider batch APIs.** Some providers offer asynchronous batch
  endpoints at a discount. They fit single-shot generation (e.g. Epic
  33's label-first generation, or judge grading) but not multi-turn
  agent loops with tool calls between turns, so they don't apply to the
  runs themselves.
- **Storage growth.** 1,000 rows × 3 trials of full step traces is
  significant. Keep full traces for failed, flaky, and sampled passing
  rows; keep only the grade and key metrics for the rest, configurable.
- Default caps (max rows per batch, max trials, max concurrency) should
  be configuration with conservative defaults suited to the local-first
  stack (Epic 9).
