# Digital Twin Manual Test Plan

Scope: Epic 14 (twin scenarios/runs), Epic 17 (unified run lifecycle), Epic 20
(agent governance), Epic 21 (twin environments & fault injection), Epic 23
(agent lifecycle gate). Assumes the app is running via `docker compose up -d`
at `http://localhost:8000` (API) / `http://localhost:3000` (web).

## Prerequisites (assumed already done)

- A process exists with at least one uploaded document that finished
  ingestion (`status: "done"`).
- A BPMN diagram has been generated and finalized (at least one version).
- A blueprint overlay has been generated for that finalized version, so the
  process has agent artifacts (`GET /api/processes/{process_id}/blueprint/agent-artifacts`
  returns a non-empty list).

If you don't have one handy, this plan was validated end-to-end against the
existing **HROnboarding** process, which already satisfies all of the above:

| Thing | ID |
|---|---|
| Process | `proc_1f0631bfae26` |
| Target agent artifact | `agent_a1c7366c8212` ("Payroll & Benefits Data Sync Agent") |

Swap in your own process/artifact IDs if testing a different process.

---

## What's already been accomplished (as of this test pass)

- Confirmed the app runs fully in local Docker (`axyntro-db-1`, `axyntro-api-1`, `axyntro-web-1`).
- Found and closed a **staleness gap**: the running `axyntro-api` image had been
  built before the latest commit (Epic 23, agent lifecycle gate) landed.
  Rebuilt via `docker compose up -d --build` so the running container matches
  the repo's `HEAD`.
- Mapped the full digital-twin API surface (governance → environment → scenario
  → run → grading → baseline/summary) against the real HROnboarding process
  and its 13 generated agent artifacts.
- Confirmed the **`SIMULATION_READY` gate** (Epic 23) is enforced: a twin run is
  refused (`400`) until an artifact's governance checklist is fully satisfied
  (tool contracts, permissions, escalation policy, model policy, success
  criteria, completion conditions, and at least one scenario).
- Drove one artifact (`agent_a1c7366c8212`) through governance patching,
  environment/fault-catalog creation, and scenario creation to reach
  `SIMULATION_READY`.
- **Found and fixed a real bug**: `backend/app/twin/schema_inference.py` capped
  its tool-schema-inference LLM call at `max_tokens=4000`, which silently
  truncated the JSON response and hard-failed **every** twin run for an
  artifact needing 3+ tool systems (100% reproducible, not flaky — confirmed
  via `output_tokens: 3998/4000` in the usage log on two separate failed
  runs). Fixed by raising it to `16000`, matching the convention already used
  by every other multi-item structured-JSON call in the codebase
  (`blueprint/service.py`, `gap_analysis/service.py`, `chat/service.py`,
  `ingestion/structuring.py`). Rebuilt and re-verified: the run now reaches
  `COMPLETED`.
- Verified, on a real run, that: the fault-injection catalog deterministically
  intercepts calls (a `timeout` fault at `probability: 1.0` fired on all 3
  attempts), the agent escalates to a human checkpoint on repeated failure,
  auto-approval (`approve_probability: 1.0`) resumes it correctly, and the
  agent degrades gracefully (returns a partial result) rather than crashing
  when a system stays down.
- The `max_tokens` fix is applied on disk (`backend/app/twin/schema_inference.py`)
  but **not yet committed to git** — pending decision on whether/how to commit.

---

## Step-by-step test plan

All calls assume a session cookie from step 1 stored at `/tmp/cookies.txt` and
base URL `http://localhost:8000`. Replace `{process_id}` / `{artifact_id}`
with your own, or use the worked-example IDs above.

### Step 1 — Login
```bash
curl -s -c /tmp/cookies.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"name":"<your name>","role":"editor"}'
```
Data point: `role` must be `"editor"` — twin/governance endpoints reject `"viewer"`.

### Step 2 — Pick a target agent artifact and check its checklist
```bash
curl -s -b /tmp/cookies.txt \
  http://localhost:8000/api/processes/{process_id}/blueprint/agent-artifacts
```
Look at the target artifact's `lifecycle_status` and `checklist` array. A
freshly-generated artifact (generated before Epic 20/23 governance existed, or
never patched) will show `lifecycle_status: "DRAFT"` with these items
`satisfied: false`:
- `success_criteria`, `completion_conditions`, `tool_contracts`,
  `permissions`, `escalation_rules`, `model_policy`, `test_scenarios`

### Step 3 — Fill governance gaps
```bash
curl -s -b /tmp/cookies.txt -X PATCH \
  http://localhost:8000/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/governance \
  -H "Content-Type: application/json" -d @governance_patch.json
```
`governance_patch.json` — adapt `system_name`/`resource` to match the target
artifact's own `tools_systems_needed`:
```json
{
  "tool_contracts": [
    {"system_name": "Payroll System API", "resource": "payroll", "action": "update", "side_effect": "mutating", "sandbox_mode": "proxy"},
    {"system_name": "Benefits Administration System", "resource": "benefits", "action": "update", "side_effect": "mutating", "sandbox_mode": "proxy"},
    {"system_name": "Onboarding DB", "resource": "onboarding_case", "action": "read", "side_effect": "read_only", "sandbox_mode": "proxy"}
  ],
  "permissions": [
    {"resource": "payroll", "actions": {"update": true}},
    {"resource": "benefits", "actions": {"update": true}},
    {"resource": "onboarding_case", "actions": {"read": true}}
  ],
  "escalation_policy": {
    "escalate_when": ["Payroll API returns an error", "Required compensation data is missing"],
    "escalation_target": {"type": "human", "role": "HR Process Owner"}
  },
  "model_policy": {"capability": "reasoning", "provider": "openrouter", "model": "qwen/qwen3.7-flash", "temperature": 0.2, "fallback_enabled": true},
  "success_criteria": "Payroll profile and benefits enrollment both created with confirmation IDs recorded on the case.",
  "completion_conditions": "Stop once both confirmations are recorded, or escalate after 2 failed attempts."
}
```
Expect the response's `checklist` to show every item `true` except
`test_scenarios` (handled in Step 5).

### Step 4 — Create a Twin Environment with a fault catalog (Epic 21)
```bash
curl -s -b /tmp/cookies.txt -X POST \
  http://localhost:8000/api/processes/{process_id}/twin-environments \
  -H "Content-Type: application/json" -d @environment.json
```
`environment.json`:
```json
{
  "name": "HR Onboarding - Payroll flaky network",
  "systems": ["Payroll System API", "Benefits Administration System", "Onboarding DB"],
  "faults": {
    "Payroll System API": [
      {"type": "timeout", "probability": 1.0}
    ]
  }
}
```
Data point: `probability: 1.0` makes the fault deterministic — every call to
that system fails, which is what makes Step 8's fault-injection check
reproducible instead of a coin flip. Note the returned `id` (e.g.
`twinenv_xxx`) and `version` (starts at `1`).

### Step 5 — Create a scenario referencing the environment
```bash
curl -s -b /tmp/cookies.txt -X POST \
  http://localhost:8000/api/processes/{process_id}/agent-artifacts/{artifact_id}/scenarios \
  -H "Content-Type: application/json" -d @scenario.json
```
`scenario.json`:
```json
{
  "name": "Payroll timeout - resilience check",
  "inputs": {
    "Hiring Details": {"employee_id": "E1042", "role": "Software Engineer"},
    "Compensation Plan": {"base_salary": 95000, "currency": "USD"}
  },
  "system_stubs": {
    "Payroll System API": {"mode": "static", "static_responses": [{"match": {}, "response": {"status": "created", "profile_id": "PAY-9981"}}]},
    "Benefits Administration System": {"mode": "static", "static_responses": [{"match": {}, "response": {"enrollment_id": "BEN-4432"}}]}
  },
  "human_checkpoint_config": {"mode": "probability", "approve_probability": 1.0},
  "expected_steps": [
    {"kind": "tool_call", "target": "Payroll System API"},
    {"kind": "tool_call", "target": "Benefits Administration System"}
  ],
  "expected_outputs": {"Payroll Profile Creation Confirmation": "PAY-9981", "Benefits Enrollment Initiation": "BEN-4432"},
  "environment_id": "<env id from step 4>",
  "environment_version": 1
}
```
> Note on grading: this scenario's `expected_outputs` assumes Payroll
> *succeeds* (`PAY-9981`), but Step 4's fault makes it *always* fail. That's
> intentional here — it means `graded_passed` will come back `false` on
> `task_completion`/`output_correctness` even though the run itself completes
> successfully and behaves correctly. Use this scenario to test **execution
> and resilience**, not to get a green "passed" grade. If you want a scenario
> that's designed to pass, don't attach a 100%-probability fault to a system
> whose expected output you're asserting.

Note the returned scenario `id` (e.g. `twinsc_xxx`).

### Step 6 — Confirm SIMULATION_READY
```bash
curl -s -b /tmp/cookies.txt \
  http://localhost:8000/api/processes/{process_id}/blueprint/agent-artifacts
```
The target artifact's `lifecycle_status` should now read `"SIMULATION_READY"`.

### Step 7 — Start the run
```bash
curl -s -b /tmp/cookies.txt -X POST \
  http://localhost:8000/api/processes/{process_id}/scenarios/{scenario_id}/run \
  -H "Content-Type: application/json" -d '{}'
```
Returns a `TwinRun` in `CREATED`. Note the `id` (e.g. `simrun_xxx`).

### Step 8 — Poll until terminal
```bash
curl -s -b /tmp/cookies.txt \
  http://localhost:8000/api/processes/{process_id}/twin-runs/{run_id}
```
Poll every 5-8s until `status` is one of: `COMPLETED`, `FAILED`, `CANCELLED`,
`TIMED_OUT`, `ESCALATED`, or `WAITING_FOR_HUMAN` (only expected if you set
`human_checkpoint_config.mode` to `"manual"` instead of `"probability"`).

### Step 9 — Inspect the trace
In the response's `steps` array, confirm:
- A `TOOL_FAILED` step on `Payroll System API` with
  `reason: "Simulated fault: timeout"` for each attempt — proves the fault
  catalog actually intercepts the call before `system_stubs` resolution.
- `HUMAN_APPROVAL_REQUESTED` → `HUMAN_APPROVED` pairs after repeated
  failures — proves escalation-on-exception routing works.
- A `TOOL_EXECUTED` step on `Benefits Administration System` with
  `result.enrollment_id == "BEN-4432"` — proves the static stub resolves
  normally on a system with no fault attached.
- `FINAL_RESPONSE` / `RUN_COMPLETED` — proves the agent didn't hang or crash
  even though one system never recovered.
Check `evaluation.dimensions` for the graded breakdown (expect
`resilience: true`, `policy_compliance: true`,
`tool_selection_correctness: true`; `task_completion`/`output_correctness`
`false` per the Step 5 note above).

### Step 10 — Deterministic fault re-verification
Repeat Steps 7-9 two more times against the **same** scenario. Since
`probability: 1.0`, every run must show the Payroll timeout on every attempt —
this is the concrete pass/fail check for the fault catalog's determinism (not
just "sometimes reproduces").

### Step 11 — Manual human-checkpoint / resume path
Create a second scenario (or edit `human_checkpoint_config` before creating
one) with:
```json
"human_checkpoint_config": {"mode": "manual"}
```
Run it. Expect `status: "WAITING_FOR_HUMAN"` instead of auto-resolving. Then:
```bash
curl -s -b /tmp/cookies.txt -X POST \
  http://localhost:8000/api/processes/{process_id}/twin-runs/{run_id}/resume \
  -H "Content-Type: application/json" -d '{"decision": "approve"}'
```
Confirm the run proceeds to a terminal status afterward.

### Step 12 — Cancel path
Start one more run, then immediately:
```bash
curl -s -b /tmp/cookies.txt -X POST \
  http://localhost:8000/api/processes/{process_id}/twin-runs/{run_id}/cancel
```
Confirm a follow-up `GET .../twin-runs/{run_id}` reports `status: "CANCELLED"`.

### Step 13 — Baseline comparison
```bash
curl -s -b /tmp/cookies.txt -X PUT \
  http://localhost:8000/api/processes/{process_id}/agent-artifacts/{artifact_id}/baseline \
  -H "Content-Type: application/json" \
  -d '{"typical_time_seconds": 900, "error_rate": 0.05, "notes": "Manual payroll entry by HR coordinator, historical average"}'
```
Then:
```bash
curl -s -b /tmp/cookies.txt \
  http://localhost:8000/api/processes/{process_id}/agent-artifacts/{artifact_id}/twin-summary
```
Confirm `run_count`, `pass_rate`, `total_cost_usd`, and
`baseline_comparison.time_delta_seconds` / `error_rate_delta` are populated.

### Step 14 — Cleanup (optional)
```bash
curl -s -b /tmp/cookies.txt -X DELETE \
  http://localhost:8000/api/processes/{process_id}/scenarios/{scenario_id}
curl -s -b /tmp/cookies.txt -X DELETE \
  http://localhost:8000/api/processes/{process_id}/twin-environments/{environment_id}
```
