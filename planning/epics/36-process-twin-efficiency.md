# Epic 36 -- Process Digital Twin at Volume: Efficiency & Readiness

**Status: implemented (2026-10-13), except US36.9 (still open).** Groomed
2026-10-13 while building the multi-agent "Invoice to Pay" demo
(`demo-deck/process-twin/`). That demo needed to run a whole process,
with 9 agents, sample data and simulated systems, end to end, many cases
at a time. Doing so exposed the gaps below. Built in `app/twin/records.py`
(US36.1), `app/twin/orchestrator.py` (US36.2, US36.3),
`app/process_twin/` (US36.4-US36.7), `app/api/process_twin.py`,
migration `a6b7c8d9e0f1`, and `frontend/src/pages/ProcessTwinPage.tsx`
(US36.8).

Goal: let a Process Owner run the *whole* agentic process as a digital
twin of the real one, over hundreds of realistic cases, before anything
reaches production. They should then be able to say, with evidence, how
much faster, cheaper and more consistent the to-be process is than the
as-is process, and where it still breaks.

Depends on: Epic 16 (orchestration rehearsal), Epic 17 (run model),
Epic 21 (environments), Epic 32 (labelled datasets), Epic 33 (decision
rules), Epic 34 (job queue, budget caps, seeds), Epic 35 (Wilson
intervals). Feeds: Epic 23 (release gate, as process-level evidence).

## Why

Epics 14-35 can prove that *one agent* is correct, at volume, with known
confidence. A Process Owner deciding whether to automate a process asks
different questions:

- **Does the whole thing work together?** Nine agents that each pass
  their own tests can still fail as a process. Agent 3 may never receive
  what agent 6 needs, or a gateway may route on a field nobody sets.
- **Is it actually better?** "86% automatable" (the blueprint) is a
  forecast. Cycle time, people's time, and cost per case, measured over
  realistic cases against today's process, are evidence.
- **Where does it break?** Which step fails most, which step still needs
  people most, and which cases fall out of straight-through processing.

Epic 16 already walks the process graph with real agents, but only for one
hand-configured case at a time:

1. **Gateways are fixed per scenario** (`gateway_decisions`). One case
   says "take the No branch at Matched?". That can't run 200 different
   invoices, where the route depends on each invoice.
2. **Hand-offs pass only the previous agent's output.** The payment agent
   needs the invoice's due date, which the intake agent extracted five
   steps earlier. Real processes carry a case file forward.
3. **Simulated systems are invented per call.** Proxy mode has the LLM
   make up a plausible vendor record. A twin of a real process needs the
   *same* vendor master, purchase orders and ledger every time, so the
   ground truth means something. Epic 21's environments already have a
   `datasets` field, but nothing reads it.
4. **There is no as-is baseline at process level.** US14.5 records one
   agent's manual time. Nothing records the people, handling times,
   waits and costs of the current process, so nothing can say "better".
5. **Nothing runs many cases through the whole process.** Epic 34's
   batches are single-agent only.

## User Stories

US36.1 -- Simulated systems backed by sample data.
As an Automation Architect, I want to give a twin environment sample
records for each system: a vendor master, open purchase orders, the
invoice ledger, the bank's payment file. Agents' tool calls then look up
and write those records deterministically instead of an LLM inventing a
response, so every case sees a consistent world and ground truth holds.
Acceptance: an environment's `system_data` maps a system name (plus
aliases) to records, lookup fields and allowed operations; the agent's
tool for that system is generated from it (no LLM schema inference);
lookups match on any provided lookup field (case and number-format
insensitive); create/update calls are acknowledged with a reference and
visible to later lookups in the same run; Static and API stubs still win
over sample data; no LLM call is made for these systems.

US36.2 -- Case file hand-off.
As an Automation Architect, I want a rehearsal to carry a case file (the
start inputs plus every agent's output so far) and hand each agent what
it needs from it. Input fields whose names already exist in the case
file are copied directly; the LLM adapter is only called for fields that
are missing. That way later agents see earlier facts, and most hand-offs
cost nothing.
Acceptance: `handoff: "case_file"` on an orchestration scenario; mapping
mode `auto` (exact names first, LLM for the remainder only); the
AGENT_DELEGATED step records which fields were copied and which adapted.

US36.3 -- Data-driven gateways.
As a Process Owner, I want each case's route through a gateway to come
from the case itself. Either the confirmed decision rules from Epic 33 are
evaluated against the case file (policy thresholds, like "amount above
EUR 10,000"), or the agent before the gateway chooses a branch, because
the routing decision is that agent's job (like "matched within
tolerance?"). One scenario then runs any number of different cases.
Acceptance: per-gateway `gateway_routing` of `configured` / `rules` /
`agent` (with a scenario-wide default); in `agent` mode the preceding
agent is told the branch labels and must return one in `route`; the
DECISION step records the mode, the rule fired or the agent's choice;
an unresolvable gateway falls back to the configured decision, else
fails the case with a readable reason.

US36.4 -- As-is operating model.
As a Process Owner, I want to record how the process runs today: who
does each step, how long it takes (minimum, most likely, maximum), how
long work waits before it, what that person's time costs, how many cases
arrive per month, and how long a reviewer spends when an agent asks for
review. The twin can then compare the to-be process against reality, not
against zero.
Acceptance: one versioned operating model per process; per-node role,
handling minutes (triangular), wait minutes (triangular), hourly cost,
and review minutes for agent nodes; process-level monthly volume,
productive hours per FTE, and end event -> outcome mapping; the table is
pre-filled from the finalized diagram's nodes and lanes.

US36.5 -- Run the process twin over a dataset.
As a Process Owner, I want to run the whole process over a dataset of
cases (all of them, or a stratified sample), each case through every
agent, with a cost estimate, budget cap, pause and cancel, and a seed per
case, so a twin run of 200 invoices is one action and can be repeated
exactly.
Acceptance: a process twin batch = orchestration scenario x dataset rows,
one `orchestration` run per case on the job queue; the per-case seed
drives manual-node and reviewer decisions; budget cap and cancel stop
unstarted cases; progress is visible while running.

US36.6 -- Simulated clock and per-case efficiency.
As a Process Owner, I want each case's to-be timeline put next to what the
same case would cost today. To-be time is agents' measured time, plus
reviewer time when an agent asks for review, plus people's handling and
waiting on manual steps. As-is time is people's handling and waiting on
every step the case passes through. Both are sampled from the operating
model, so the comparison is like for like.
Acceptance: per case: as-is and to-be cycle time, people's (touch) time,
labour cost, LLM cost, number of human touches, straight-through flag.
Sampling is seeded per case, so it is reproducible.

US36.7 -- Efficiency and readiness report.
As a Process Owner, I want one report: straight-through rate, outcome
accuracy against the dataset's labels, cycle-time and people-time
reduction, cost per case, and annualised hours, FTE capacity and cost
released at the stated volume. Each figure needs a confidence range, plus
a per-step table showing which step fails, costs or needs people most. A
case table should drill into each case's full run. I can then decide
whether this process is ready, and what to fix first.
Acceptance: Wilson intervals for rates, bootstrap intervals for mean
time/cost savings; per-node failure rate, mean agent seconds, review
rate, LLM cost; misrouted cases listed with expected and actual outcome;
annualised figures state their assumptions.

US36.8 -- Process twin page.
As a Process Owner, I want a "Process twin" page per process with three
parts: the operating model (editable table), run (choose scenario,
dataset, sample, budget), and results (headline as-is vs to-be, per-step
table, case list linking to each orchestration run).

US36.9 -- Capacity and queueing (not built).
As a Process Owner, I want the twin to simulate arrivals and staff
capacity, a true discrete-event simulation with queues, so that waiting
time comes from load rather than from a stated distribution. I could
then ask "what if volume doubles at month end?".
Not built: today's wait times are sampled from the operating model, so
the twin measures handling efficiency, not congestion. See Notes.

## Hardening found by running it live (2026-10-13)

Building the demo against the real model surfaced defects that the mocked
tests could not. Each is fixed, tested and logged in
`planning/decision-log.md`:

- **Blueprint evaluation is chunked** (8 nodes per call, the rest of the
  diagram as context), with one retry on a malformed reply. A 20-node
  diagram with a large context pack ran past the output cap.
- **Only task nodes get a runnable agent** in the walk. An agent the
  blueprint attached to an end event blocked every run.
- **The data adapter's token cap was raised** from 1,000 to 8,000.
- **Agents get the case file for reference** alongside their declared
  inputs, because blueprint input names are loose.
- **Nested agent steps are part of the run trace** (an Epic 17 defect).
  Without that fix, the efficiency report undercounted review time.
- **One recorded repair turn** when an agent's final answer isn't JSON.
- **US20.8** (Epic 20): an agent's systems can be changed without
  regenerating it.

## Notes / Open Questions

- **Parallel gateways remain unsupported** (Epic 16's "Still open").
  The demo process is sequential. A real AP process often runs supplier
  checks and PO matching in parallel. Supporting fork/join needs the
  walk to keep one case file per branch and merge them at the join.
- **Why sample-data systems are in-process, not mock HTTP servers.** API
  mode (US29.5) already covers real sandbox endpoints. Most customers
  don't have sandboxes for every system, but do have extracts (CSV).
  In-process records make "upload the vendor master extract" the whole
  setup, with no server to run.
- **Agent-routed gateways ask the agent to choose by branch label.** The
  label is the business wording on the diagram ("No -- exception"). The
  agent's answer is matched case- and punctuation-insensitively, and an
  unknown answer falls back rather than guessing.
- **The as-is comparison follows the same path.** As-is time is computed
  over the path the case took in the twin. That's right when the twin
  routes correctly; for misrouted cases the report flags that the
  comparison is approximate.
- **Annualised savings are an extrapolation** from the sample, at the
  stated monthly volume and FTE hours. The report says so next to the
  number.
