# Digital Twin Simulation — UI Test Pack

Scope: the **Digital Twin Simulation** tab (`Blueprint page → Digital Twin
Simulation`) and the parts of the **Agents** tab a user must visit first to
make a scenario runnable. Every step below is a click/type/select action in
the browser at `http://localhost:3000` — no API calls, no curl. Covers Epic
14 (scenarios/runs), Epic 17 (run lifecycle), Epic 18 (run observability),
Epic 20 (governance), Epic 21 (environments/fault injection), Epic 23
(lifecycle gate).

## Prerequisites (assumed already done)

- A process exists with an uploaded, ingested document, a finalized BPMN
  version, and a generated blueprint overlay (so its **Agents** tab shows at
  least one agent card).
- You're logged in with **role: Editor** — Viewer can look but every button
  in this pack (Regenerate agent, Save environment, Add scenario, Run) is
  disabled for Viewer.
- Worked example used while validating this pack: process **HROnboarding**,
  agent card **"Payroll & Benefits Data Sync Agent"**.

## Known UI gaps (read before testing — not test failures)

Discovered while building this pack; these are documented absences, not bugs
to re-report:

1. **No "Cancel run" button** anywhere in the Digital Twin Simulation tab.
   The API supports cancelling a twin run, but `DigitalTwinPanel` never
   wires it up (the Orchestration Rehearsal tab has its own Cancel button
   for orchestration runs — twin runs don't get the same treatment). A twin
   run can only be watched to completion via the UI, not stopped.
2. **No "Resume" control for a run waiting on a manual human checkpoint.**
   If a scenario's `human_checkpoint_config` mode is `"manual"`, a run can
   land in `WAITING_FOR_HUMAN` with no button anywhere to approve/reject it
   — that control exists for orchestration runs but not twin runs. Don't set
   up a manual-checkpoint scenario expecting to resume it through the UI;
   use `"probability"` mode (the form's own default) instead.
3. **No governance edit form.** The Agents tab's lifecycle panel *shows*
   which checklist items are missing but has no fields to fill them in
   directly — the only UI-available fix is **Regenerate agent**, which
   applies the current default governance template (Test Case TC-GOV-01
   below). If you need a *specific* permission/escalation rule the default
   template doesn't produce, there's currently no UI path to set it — that's
   an API-only (`PATCH .../governance`) operation today.

---

## Part A — Get an agent to "Simulation ready"

### TC-GOV-01: Regenerate a stale/ungoverned agent artifact

**Preconditions:** On the process's Blueprint page, **Agents** tab.

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | Open the process from the process list, then open its **Blueprint** page. | — | Blueprint page loads with tabs: Blueprint, Agents (N), Digital Twin Preview, Digital Twin Preview Orchestrated, Digital Twin Simulation, Orchestration Rehearsal. |
| 2 | Click the **Agents (N)** tab. | — | A grid of agent cards appears, one per automatable/partial blueprint node. |
| 3 | Click an agent card, e.g. **"Payroll & Benefits Data Sync Agent"**. | — | The detail panel on the right fills in: purpose, trigger, system prompt, tools, rationale, governance summary. |
| 4 | Scroll to the lifecycle badge below "What governance would it need?". | — | A status badge shows one of: Draft, Validated, Simulation ready, Simulating, Evaluated, Release candidate, Approved, Deployed. If not yet "Simulation ready" or later, a bulleted list titled "Missing: ..." appears underneath, e.g. "Missing: Tool contracts", "Missing: Test scenarios". |
| 5 | Click **Regenerate agent** (button just above the lifecycle badge). | — | Button shows "Generating..." then re-enables. |
| 6 | Re-check the lifecycle badge/checklist. | — | All "Missing: ..." lines disappear except possibly "Missing: Test scenarios" (that one is only satisfied once a scenario exists — Part B). Badge should now read "Simulation ready" once a scenario exists, or still show the Test scenarios gap until then. |

---

## Part B — Digital Twin Simulation tab

Click the **Digital Twin Simulation** tab. You land on a per-agent view: a
row of agent-name tabs at the top (one per agent artifact) — click the one
you want to test (e.g. "Payroll & Benefits Data Sync Agent").

### TC-ENV-01: Create a reusable Digital Twin Environment with a fault

Scroll to the **"Digital twin environments"** section at the bottom of the
tab.

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | Click **New environment**. | — | A form appears: Name, Systems (comma-separated), Datasets (JSON), Personas (JSON), Events (JSON list), Faults (JSON). |
| 2 | Fill **Name**. | `HR Onboarding - Payroll flaky network` | — |
| 3 | Fill **Systems (comma-separated)**. | `Payroll System API, Benefits Administration System, Onboarding DB` | — |
| 4 | Leave Datasets/Personas as `{}` and Events as `[]`. | — | — |
| 5 | Fill **Faults (JSON, keyed by system name)**. | `{"Payroll System API": [{"type": "timeout", "probability": 1.0}]}` | — |
| 6 | Click **Save environment**. | — | Form closes; a new card appears in the environments list: "HR Onboarding - Payroll flaky network — v1", subtext "3 system(s) — 1 with declared fault(s)". |

**Data note:** `probability: 1.0` is deliberate — it makes the fault fire on
*every* call, so Test Case TC-RUN-02 (determinism check) has something
concrete to verify instead of a coin flip.

### TC-ENV-02: Edit an existing environment

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | On the environment card from TC-ENV-01, click **Edit**. | — | The form re-opens pre-filled with the card's current values. |
| 2 | Change the fault probability. | `0.5` | — |
| 3 | Click **Save environment**. | — | Card updates; version label stays the same (`update` doesn't bump the shown counter unless the backend does — verify the card's `v#` matches what you expect). |
| 4 | Click **Cancel** on a fresh edit instead of saving. | — | Form closes with no changes applied. |

### TC-SCN-01: Create a scenario referencing the environment

Scroll up to the **"New scenario"** form (above the environments section).

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | Fill **Name**. | `Payroll timeout - resilience check` | — |
| 2 | Fill **Inputs (JSON)**. | `{"Hiring Details": {"employee_id": "E1042", "role": "Software Engineer"}, "Compensation Plan": {"base_salary": 95000, "currency": "USD"}}` | — |
| 3 | Fill **System stubs (JSON, keyed by system name)**. | `{"Payroll System API": {"mode": "static", "static_responses": [{"match": {}, "response": {"status": "created", "profile_id": "PAY-9981"}}]}, "Benefits Administration System": {"mode": "static", "static_responses": [{"match": {}, "response": {"enrollment_id": "BEN-4432"}}]}}` | — |
| 4 | Leave **Human checkpoint config (JSON)** at its default. | `{"mode": "probability", "approve_probability": 1.0}` | Auto-approves any checkpoint the agent raises — needed since there's no manual-resume UI (see Known Gaps). |
| 5 | Fill **Expected steps (JSON list)**. | `[{"kind": "tool_call", "target": "Payroll System API"}, {"kind": "tool_call", "target": "Benefits Administration System"}]` | — |
| 6 | Fill **Expected outputs (JSON)**. | `{"Payroll Profile Creation Confirmation": "PAY-9981", "Benefits Enrollment Initiation": "BEN-4432"}` | See note below. |
| 7 | In the **Environment** dropdown, select the environment from TC-ENV-01. | `HR Onboarding - Payroll flaky network (v1)` | — |
| 8 | Click **Add scenario**. | — | Form clears (Name and Environment reset); a new card appears under "Scenarios": name, no status badge yet, buttons Run / Delete. |

> **Grading note:** step 6's expected outputs assume Payroll *succeeds*
> (`PAY-9981`), while the environment's fault makes it *always* fail. That
> mismatch is intentional for this scenario — it exists to prove resilience
> (the agent keeps going and produces a sensible partial result), not to get
> a green "passed" grade. Expect `graded_passed: false` after running it; if
> you want a scenario that's designed to pass cleanly, don't pair a
> 100%-probability fault with an expected-success output for the same field.

### TC-SCN-02: Reject malformed JSON in the scenario form

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | Repeat TC-SCN-01 but type invalid JSON into **Inputs (JSON)**. | `{invalid` | On submit, an error message appears below the form: "Inputs is not valid JSON". Scenario is not created. |

### TC-RUN-01: Run the scenario and inspect the trace

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | On the scenario card from TC-SCN-01, click **Run**. | — | Button changes to "Running..." and disables. |
| 2 | Wait (poll interval is 2s; the run itself takes roughly 40-70s across a few model turns). | — | A status badge appears next to the scenario name; it should land on **COMPLETED** (not FAILED — if it's FAILED with a JSON-parsing error mentioned in the deviation, see the note at the very bottom of this document). |
| 3 | Below the scenario card, check the deviations list (if any) and the summary line. | — | Summary line reads like "N step(s) — M turn(s) — $X.XXXX". If deviations are listed, expect two "Unexpected extra step" entries and two "Expected output field ... was ... expected ..." entries — consistent with the TC-SCN-01 grading note. |
| 4 | Click **View run**. | — | `RunDetailView` expands: a Process / Agent / Technical view switcher, a step list, and a step inspector panel. |
| 5 | In the step list (default "Process" view), find and click the step describing the Payroll failure. | — | Step inspector shows detail consistent with a simulated timeout fault. |
| 6 | Switch to the **Technical** view. | — | Step list re-labels each row as `<step type> · <target>`, e.g. `tool failed · Payroll System API`, `human approval requested · human_checkpoint`, `human approved · human_checkpoint`. |
| 7 | Switch to the **Agent** view. | — | A different narration style of the same steps (agent-centric wording). |
| 8 | Scroll down to the **Evaluation** section. | — | Header reads "Evaluation (N/12 dimensions passed)". Expect `resilience`, `policy_compliance`, `tool_selection_correctness`, `human_intervention`, `latency`, `token_usage`, `cost` badges green; `task_completion`, `output_correctness` red (per the TC-SCN-01 grading note); `efficiency` may be red if the retries pushed step/tool-call counts past the guardrail limits. |
| 9 | Expand the **Reproducibility** `<details>` section. | — | Loads and shows reproducibility tiers with badges and an explanation per tier, plus a JSON configuration snapshot (or a note that no snapshot was recorded, for a pre-Epic-22 run). |
| 10 | Use the replay scrubber (◄◄ / ▶ / ►►, or drag the range slider). | — | Selected step highlights in the step list; the "at this point: N tokens · $X.XXXX · Ys elapsed" caption updates as you scrub. |
| 11 | Click **Hide run**. | — | `RunDetailView` collapses back to just the summary line. |

### TC-RUN-02: Fault determinism — repeat the run

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | Click **Run** on the same scenario two more times. | — | Each run reaches a terminal status. |
| 2 | View each run's trace (TC-RUN-01 steps 4-6). | — | **Every** run must show the Payroll timeout fault firing on **every** attempt at that system — since `probability: 1.0`, zero tolerance for it ever succeeding. If any run shows Payroll succeeding, that's a real defect in fault-injection determinism. |

### TC-DEL-01: Delete a scenario

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | Click **Delete** on a scenario card. | — | Browser confirm dialog: "Delete this scenario? This cannot be undone." |
| 2 | Confirm. | — | Card disappears from the Scenarios list. |
| 3 | Repeat, but dismiss/cancel the confirm dialog instead. | — | Card remains. |

### TC-BASE-01: Set and compare an as-is baseline

Scroll to the **Twin summary** card at the top of the tab (only visible once
a summary exists for the artifact, i.e. after at least one run).

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | Confirm the summary shows "N run(s)", a pass rate percentage (or "n/a"), and total cost. | — | Matches the number of runs executed in TC-RUN-01/02. |
| 2 | Under "As-is baseline (manual estimate)", fill **Typical time to complete (seconds)**. | `900` | — |
| 3 | Fill **Error rate (%)**. | `5` | — |
| 4 | Fill **Notes**. | `Manual payroll entry by HR coordinator, historical average` | — |
| 5 | Click **Save baseline**. | — | Button shows "Saving..." then re-enables; a **"Clear baseline"** button appears next to it. |
| 6 | Check for a new **"Twin vs. baseline"** section. | — | Shows "Average run: Xs (±Ys vs. baseline)" and "Error rate delta: ±Npp vs. baseline", computed from the runs already recorded. |
| 7 | Enter an out-of-range error rate. | `150` | On submit: error "Error rate must be a percentage between 0 and 100"; baseline not saved. |
| 8 | Click **Clear baseline**. | — | Confirm dialog: "Clear the baseline estimates and notes? This cannot be undone." On confirm, all three fields empty out and the "Twin vs. baseline" section disappears. |

### TC-SWITCH-01: Switch between agent artifacts

| Step | Action | Data | Expected result |
|---|---|---|---|
| 1 | At the top of the Digital Twin Simulation tab, click a different agent's name tab (e.g. "IT Provisioning Orchestrator Agent"). | — | Scenarios list, twin summary, and environments panel reload for that artifact; environments list itself doesn't change (environments are process-wide, not per-agent). |

---

## Bonus / adjacent panels in the same tab (optional, lower priority)

These render inside the Digital Twin Simulation tab but belong to other
epics — test only if time allows:

- **Regression suite panel** (below the scenarios list): lets you bundle
  several scenarios into a named suite and run them together. Out of scope
  for this pack's focus on single-scenario twin testing.
- **Compare runs**: pick two runs from dropdowns to diff their step counts,
  cost, and outcome side by side.

---

## If a run unexpectedly FAILS at TC-RUN-01 step 2

If the run's status lands on `FAILED` and the only step recorded is missing
(no trace at all), open the run and check for a deviation reading roughly
`LLM returned invalid structured output for tool schemas: ...`. This was a
real, previously-found-and-fixed bug (`backend/app/twin/schema_inference.py`
had `max_tokens=4000`, too low for 3+ tool systems, silently truncating the
JSON). If you see it recur, the fix may not be deployed to the container
you're testing against — confirm with whoever manages the environment
whether `docker compose up -d --build` has been run since that fix landed.
