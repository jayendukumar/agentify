# Epic 39 -- Agent Runtime: Running Approved Agents in Production

**Status: groomed 2026-10-17. Steps 1-3 implemented 2026-10-17: one
executor (US39.1); deployments, executions, the API trigger, identity,
adapters, credentials, guardrails failing closed, the kill switch
(US39.2-39.8, US39.11); the review inbox, limits and circuit breakers,
automatic pause (US39.9, US39.10, US39.12). US39.4 completed 2026-10-17
(typed input, internal triggers, client limits, signed callbacks, OAuth
client credentials from the tenant's IdP). Step 4 implemented 2026-10-17:
shadow, canary and rollback, live metrics, traces and alerts, drift
against the twin, and the Runtime page (US39.13-39.15). Step 5 started
2026-10-17: event triggers (US39.16), certified hand-off (US39.18). Open
items are listed under each step's "as built". Decisions confirmed 2026-10-17 (see Notes).**
Weakness 3 in the wiki's Critical Review ("Agents never run in
production") and item 4 of its recommended order.
Section 3 of the enterprise-readiness deck ("Production agent runtime").

Goal: an agent that passed the twin and was approved can run for real,
against the customer's real systems, as itself. It acts under its own
identity with scoped, short-lived credentials, and it is held to the
guardrails it was tested with. It can be stopped in seconds. Its live
behaviour is watched and compared with what the twin measured. The code
that runs it in production is the code the twin tested.

Depends on: Epic 20 (Tool Gateway, guardrails, escalation policy, tool
contracts), Epic 17 (one run/step model, manual pause and resume), Epic
22 (run snapshots), Epic 23 and Epic 35 (APPROVED state, statistical
release gate), Epic 29 (applications, OpenAPI operations, endpoint
environments), Epic 37 (permissions, segregation of duties, audit), Epic
38 (tenant context for every request and job, per-tenant secrets and
limits). Strongly recommended first: the Critical Review's item 2 (a
worker separate from the API, append-only run steps), because a
production run must not die with an API redeploy (see US39.3). Feeds: a
hosted pilot where agents do real work.

## Why

Measured on 2026-10-17 against the code:

- **"DEPLOYED" is a label.** `POST /publications/{id}/mark-deployed`
  (`app/api/publish.py`) records that someone says an artifact is
  deployed. Nothing runs it. There's no table of what is running where,
  at which version, or since when.
- **The only execution loop is the twin's.** `run_scenario`
  (`app/twin/engine.py`, about 350 lines) is the loop that runs model
  turns, tool calls, guardrails, checkpoints and escalation. Twin-only
  concerns are mixed into it: fault injection, Proxy/Static stubs,
  sample-data records, the seeded reviewer, grading. A production
  executor written separately would drift from it on day one, and what
  ran in production would no longer be what was tested.
- **Governance fails open.** `check_tool_permission` allows every tool
  when an artifact declares no tool contracts (`app/twin/gateway.py`).
  That was right for old artifacts in a sandbox. In production it means
  an artifact with no contracts can call anything it's offered.
- **There's no outbound identity.** Twin API mode (`_call_api` in
  `app/twin/simulators.py`) sends unauthenticated requests, and
  `app/catalog/endpoints.py` *refuses* any endpoint tagged or named like
  production. OpenAPI import drops header parameters. There's no notion
  of a credential, a scope, or who the call is made as.
- **Guardrails are per run only.** `GuardrailTracker` caps steps, tool
  calls, tokens, cost and wall time inside one run. Nothing caps an agent
  across runs (calls per minute, spend per day), and nothing trips when
  one tool keeps failing.
- **Human checkpoints wait on a scenario setting.** Manual mode
  (`WAITING_FOR_HUMAN` + `resume_state`) works, but the person deciding is
  whoever presses Resume on a twin run. There's no inbox, assignee, due
  time or escalation.
- **Stopping means cancelling one run.** `cancel_requested` on a
  `simulation_runs` row stops that run at its next step. There's no way to
  stop an agent, all of a tenant's agents, or new work arriving.
- **Nothing is measured after release.** The release gate (Epic 35)
  knows the twin's pass rate, decision mix, escalation rate and cost
  distribution with confidence ranges. Nothing records the same numbers
  live, so drift can't be seen.

## Design summary

- **One executor, two sets of adapters.** The loop in `run_scenario`
  moves to `app/runtime/executor.py` with three ports:
  - `ToolPort`: resolves a tool call. Twin: faults, stubs, records, API
    mode (today's code, moved). Production: real adapters (US39.6).
  - `HumanPort`: resolves a checkpoint. Twin: sampled, rule or manual.
    Production: the review inbox (US39.10).
  - `StepSink`: records steps. Twin: `simulation_runs`. Production: the
    execution store (US39.3).

  The model loop, the Tool Gateway check, `GuardrailTracker`, the repair
  turn, escalation and suspend/resume stay in the executor and are shared.
  `run_scenario` becomes a thin twin wrapper that adds grading. The whole
  existing twin test suite passing unchanged is the proof the extraction
  changed nothing.
- **A deployment is a record, not a label.** `agent_deployments` pins one
  artifact version, its snapshot (Epic 22), its adapter bindings and its
  identity. Only an APPROVED version can be deployed. Deploying is a
  permission of its own, needs someone other than the builder (Epic 37),
  and is audited. A new artifact version is a new deployment, never an
  edit to a running one.
- **Production fails closed.** No tool contracts, no adapter binding, no
  credential, an unapproved version, a killed deployment: each refuses
  the call or the run. The twin keeps its current fail-open default for
  old artifacts.
- **The agent acts as itself.** Each deployment has a workload identity:
  not a user, can't sign in, has no UI roles. Calls to a system use a
  short-lived token for that identity, scoped by the tool contract's
  resource and action, obtained from a secret reference (Epic 38 secret
  store). Every step and audit event names the agent identity and the
  caller it acts for.
- **Stop is checked where the twin checks cancel.** The executor already
  checks for cancellation before each model call and tool call. The kill
  switch adds one more check at those points, against a flag at
  deployment, agent and tenant level, plus a refusal at the front door.
- **Twin evidence is the baseline.** When a version is deployed, the
  numbers its release was approved on (Epic 35's batch results) are
  frozen as its baseline. Live metrics are computed the same way over
  rolling windows, so drift is a like-for-like comparison.

## US39.1 as built

- **Code.** `app/runtime/executor.py` holds the loop (`execute`), the
  `ToolPort` and `HumanPort` protocols, `ToolResult`, `ExecutionError`,
  `RunCancelled` and the `request_human_decision` tool.
  `app/twin/engine.py` keeps `run_scenario` with its signature, and adds
  `_TwinTools` (faults, then sample data, then static stubs, sandbox API
  or an LLM-fabricated response) and `_TwinReviewer` (sampled, rule or
  manual). It grades the outcome as before. `TwinServiceError` is now an
  `ExecutionError`, so the executor ends a run FAILED with its message,
  as before. The engine went from 654 lines to about 420.
- **Tests.** `tests/test_runtime_replay.py` runs 17 scripted cases
  (tools, policy denial, scenario and environment faults, LLM-fabricated
  responses, seeded and manual checkpoints with resume, repair turn,
  fenced JSON, each guardrail, loop detection, escalation, cancellation,
  routing and dataset decisions, sample data, stub latency). It compares
  every step, outcome, resume state and model request with
  `tests/fixtures/executor_replay.json`, recorded from the engine before
  the move. Changing one step's reason makes it fail.
  `tests/test_runtime_executor.py` drives the executor with non-twin
  ports: a denied call never reaches the tool port, a port failure ends
  the run FAILED, a checkpoint with no decision suspends and resumes. It
  also holds the import guard. The existing twin, orchestration, batch and
  process-twin tests pass without edits.

Deviations from the story, decided while building:

- **No `StepSink` class.** The `on_step` and `on_phase` callbacks the
  twin already passes are the step sink. A third protocol would only wrap
  them. Production's execution store (US39.3) supplies its own callbacks.
- **The run instructions moved too.** The twin's extra system message
  (which tools map to which systems, how to ask for review, the output
  keys) is now `app/runtime/instructions.py`'s `run_instructions`, and
  the executor takes the text as an argument. Production sends the same
  text with a different header, since the twin's says "for this test run
  only". Writing new instructions for production would void the twin
  evidence.
- **`RunCancelled` carries a status and a reason,** so a kill switch
  (US39.11) can stop a run as `KILLED` through the same path a cancel
  uses. `KILLED` is a new terminal run status.
- **The gateway stays in `app/twin/gateway.py`.** It is the one twin
  module `app/runtime` may import. Moving it would touch every governance
  import for no change in behaviour.

## Step 2 as built (US39.2-39.8, US39.11)

- **Code.** `app/runtime/`: `deployments.py` (create, bindings,
  readiness, activation with segregation of duties, kill, retire,
  identity revocation), `adapters.py` (the production ToolPort and the
  endpoint rule), `credentials.py` (OAuth client credentials and static
  keys, by secret reference), `jobs.py` (submitting a case and the
  `runtime_execution` job), `instructions.py` (moved in step 1). Routes in
  `app/api/runtime.py`: management under `/api/processes/{id}/...` and
  `/api/runtime/...`; the tenant's own systems call `/runtime/v1/...` with
  a runtime client's bearer token. Operator stop:
  `POST /api/operator/tenants/{id}/agents/stop`. Migration `d3e4f5a6b7c8`
  adds six tenant tables with row-level security. New permissions:
  `agent.deploy` (architect) and `runtime.stop` (process owner, approver,
  admin).
- **Tests.** `tests/test_runtime.py` (15): deploy, bind, activate and run
  a case; regenerating leaves a running deployment alone; deploying refuses
  an unapproved or simulated-tool agent; bindings take only tested
  operations on registered production endpoints and refuse an inline
  secret; a changing agent needs a waiver; an unset secret blocks
  activation; input is checked before any model call; OAuth tokens are
  scoped and cached, and no secret or token reaches a stored row or the
  model; the kill stops a running case after its in-flight call, stops 20
  waiting cases without a step, and refuses new work; tenant-wide kill,
  identity revocation, operator stop; a restart after a sent write
  escalates; another tenant's client gets 404. `tests/test_runtime_executor.py`
  adds the US39.8 parity test. The cross-tenant probe covers the new routes,
  calling `/runtime/v1` with tenant B's own runtime client.
- **Live.** `testing/runtime/smoke_runtime.py`, against the running stack:
  a stand-in core-banking system (`testing/runtime/core_banking_standin.py`)
  that checks Keycloak tokens from a `core-banking` realm
  (`testing/keycloak/core-banking-realm.json`), a real model, and the
  twin in API mode against the stand-in's sandbox. 21/21 on 2026-10-17:
  twin test, deploy, OAuth binding, a separate approver's activation, a
  real case (two production calls, token issued to the agent with the
  bound scopes, idempotency key on the write), then a kill while a call
  was in flight: new work refused at once, the call finished, nothing ran
  after it.

Deviations from the stories, decided while building:

- **Only OpenAPI-backed tools can be deployed.** A production adapter
  turns the model's call into an HTTP request, which needs the operation
  shape the twin tested (Epic 29's `operation` + `params` tool). An agent
  tested with an LLM-simulated tool is refused at deployment, with the
  fix named: link the system to a catalog application with a spec and test
  again.
- **The tool schemas are pinned at deployment.** Twin runs rebuild
  spec-backed tools from the current spec and record no spec version
  (Epic 22's snapshot doesn't include tools). So deployment pins the spec
  as it is, and a later change to the application's spec appears as a
  difference that blocks activation until it's accepted.
- **Scopes are per tool contract, not per operation.** A contract has one
  action, so one token serves every allowed operation of that system. In
  the live test, the agent's Core Banking contract (action `update`)
  needed `core.read` and `core.write` in one token to look up and then
  open an account.
- **A restart reruns or escalates; it doesn't resume mid-case.** The
  executor's state between turns isn't persisted, so a restarted case
  starts again as a new attempt (steps are kept per attempt), unless the
  dead attempt let a data-changing call through the gateway, in which case
  it ends ESCALATED.
- **The kill check reads the database at every step**, with no cache, so
  "within 5 seconds" holds trivially and on every instance. A kill also
  ends queued cases KILLED and cancels their jobs on the spot.
- **Runtime clients use a bearer token** (the SCIM token pattern), not
  OAuth client credentials. The token is tenant-wide; limiting a client
  to some deployments is open. (Both closed in US39.4.)
- **A human checkpoint suspends the case** (WAITING_FOR_HUMAN) with no way
  to resume it until the review inbox (US39.10, step 3).
- **Callbacks** go only to hosts in `RUNTIME_CALLBACK_HOSTS` and aren't
  signed yet.
- **Not built in this step:** retention and redaction of stored inputs,
  outputs and payloads (US39.3), setting the identity's directory client
  id (the column exists), the pause action (US39.12), and any UI. Every
  action is in the API; the Runtime page is US39.14.

## Step 3 as built (US39.9, US39.10, US39.12)

- **Code.** `app/runtime/reviews.py` (inbox tasks, who reviews from the
  approval matrix, deciding and resolving, SLA escalation, notifications,
  dataset rows), `app/runtime/limits.py` (default caps from the twin's
  evidence, the 429 check, circuit breakers, pause rules),
  `app/twin/gateway.py` (`check_argument_rules`, `check_review_before_action`),
  pause and resume in `app/runtime/deployments.py`, and in
  `app/runtime/jobs.py` the resume after a decision, the held queue while
  paused and the task each outcome opens. The worker escalates overdue
  reviews on every tick. Migration `e4f5a6b7c8d9`: `review_tasks`,
  `runtime_notifications`, `tool_circuits`, and the settings columns on
  deployments. New permission `runtime.review` (approver, process owner).
  Routes: `/api/runtime/inbox`, `/api/runtime/notifications`, and under
  each process `review-tasks/{id}/decide|resolve|to-dataset` and
  `deployments/{id}/settings|pause|resume`. The **Inbox** page
  (`frontend/src/pages/InboxPage.tsx`) lists the tasks a person may act on,
  with approve/reject for checkpoints and resolve for escalations.
- **Tests.** `tests/test_runtime_reviews.py` (13): a checkpoint waits in the
  inbox for the matrix role, the requester and a non-reviewer are refused,
  a rejection needs a comment, an approval resumes the case with the
  reviewer on the step; an amount over a limit goes to the level above;
  an overdue task moves up and notifies; an escalated case is resolved by
  hand and becomes a dataset row; a kill cancels open tasks; caps refuse
  with 429 and only an admin changes them; a failing system opens its
  circuit, the next case escalates without calling it, a probe closes it;
  argument rules; failing cases and rejected reviews pause the agent;
  queued work waits and runs after a resume; a manual pause; another
  tenant's inbox is empty. `tests/test_runtime_executor.py` adds the
  review-before-action enforcement. The probe covers `task_id`.
  `frontend/src/pages/InboxPage.test.tsx` (3).
- **Live.** `testing/runtime/smoke_runtime_reviews.py` (24/24 on
  2026-10-17), same stand-in and Keycloak realm as step 2, with an agent
  that reviews before acting: the case waited for "Branch Lead", the
  person it was for was refused, an approver approved, the case completed
  with one write to production; the stand-in then failed, the circuit
  opened after two failures, the next case was escalated without calling
  production and resolved by hand; a manual pause held a new case, which
  ran after the resume. The step 2 smoke still passes (21/21).

Found live, and fixed:

- **"Review before action" wasn't enforced.** It was only a sentence in
  the agent's prompt. In the first live run the model opened the account
  without asking. The gateway now denies a call through a data-changing
  contract until a person approved in the run, telling the model to ask
  first (`check_review_before_action`, twin and production alike). The
  twin replay is unchanged: its agents declare no contracts.

Deviations from the stories, decided while building:

- **The matrix gives a role name, not a person.** Approval authorities
  (Epic 27) name organization roles, and nothing links those to users.
  The task shows the role; anyone with `runtime.review` for the
  process's business unit may act on it. Levels follow the authorities'
  limits, lowest first, skipping those the proposed amount exceeds (the
  amount is the first `amount`/`total`/`value`-like number in the
  proposed action). The decision type isn't matched yet.
- **"The person who triggered it" is `requested_by`.** Cases come from a
  system, so the caller names the person they're for; a reviewer whose
  name or email matches is refused.
- **The model may ask more than once.** Seen live: it asked before the
  lookup and again before the write. One approval lets the gateway allow
  data-changing calls for the rest of the run; asking again is the
  model's choice. Reviewers see two tasks for that case.
- **Notifications are in the app only.** A table and
  `/api/runtime/notifications`, shown on the Inbox page; no email or
  webhook yet.
- **Caps count from the database at submission.** The concurrency cap
  counts running cases (not queued or waiting for review). A
  data-changing call cap blocks new cases, not a running one.
- **A circuit probe isn't exclusive.** After the cool-down, every worker
  may let one call through at once; the first result decides the state.
- **The drift pause trigger** waits for US39.15.
- **Retention and redaction** of stored payloads (US39.3) are still open.

## US39.4 completed (step 2's open items)

- **Code.** `app/runtime/inputs.py` checks each input field's type where
  its format names one ("number", "date", "boolean", "list of ...").
  Free-text formats ("currency code", "PDF") take any value. Missing or
  null fields are refused, and every problem is listed in the 422.
  `app/runtime/callbacks.py` turns the callback into a `runtime_callback`
  job (5 attempts with backoff). The job signs the body
  (`Axyntro-Signature: t=..,v1=HMAC-SHA256("<t>.<body>")`) with the
  client's signing secret, shown once at creation, and also runs when a
  kill ends a case that never started. `jobs.queue_submission` and the
  `runtime_submit` job start a case from an internal trigger (a scheduled
  sweep) through the same `submit`. A cap retries later; other
  refusals are audited. Runtime clients take an optional `deployment_ids`
  and get 404 outside it. Migration `f5a6b7c8d9e0` adds
  `agent_executions.trigger` and `runtime_clients.deployment_ids` and
  `signing_secret`. A production-only worker runs
  `JOB_WORKER_KINDS=runtime_execution,runtime_submit,runtime_callback`.
- **Tests.** `tests/test_runtime_triggers.py` (9): format typing; 422
  with every problem listed and no model call; a sweep through the queue
  (trigger recorded, no client, the same key twice is one case); an
  internal trigger refused like the API and audited; at a cap it retries;
  a client limited to other deployments gets 404, and an unknown id in the
  limit gets 422; a callback retried after a 500 and signed, verified by
  HMAC; a kill before start queues the callback; a callback host that
  isn't allowed gets 422.
- **Live.** `testing/runtime/smoke_runtime_triggers.py` (19/19 on
  2026-10-17), with the same stand-in and realm. The stand-in now also
  hosts the loan system's callback receiver (`/hooks/agent-done`). A
  number where the schema says string got 422. A real case's callback
  failed once and was retried, and its signature checked out. A sweep
  queued through `runtime_submit` completed with no client, and queuing
  the same key again started nothing.
- **OAuth client credentials (closed 2026-10-17).** A runtime client
  registered with `oauth_client_id` signs in with an access token from
  the tenant's own IdP (client credentials grant). The token's signature,
  issuer and expiry are checked against that IdP, the client it names
  (`azp`/`client_id`/`appid`) must be registered and unrevoked, and its
  audience must be the client's (default `RUNTIME_OAUTH_AUDIENCE`,
  `axyntro-runtime`). Bearer tokens remain for tenants without an IdP.
  Migration `f6a7b8c9d0e1`. Tests: `tests/test_runtime_oauth.py` (6).
  Live: `testing/runtime/smoke_runtime_oauth.py` (18/18 on 2026-10-17)
  with Keycloak's service account client `loan-system` in realm
  `axyntro`.

## Step 4 as built (US39.13-39.15)

- **Code.** Rolling out (`app/runtime/deployments.py`): `activate` takes
  `rollout` -- `full`, `canary` (with `share`) or `shadow` -- and
  remembers the version it replaces (`previous_deployment_id`). `promote`
  steps a shadow or canary up (5, 25, 100; at 100 the previous version
  retires), approved under the same segregation rule as activation.
  `rollback` retires a canary or shadow. Routing (`app/runtime/jobs.py`
  `route`): a case sent to any deployment id of the agent goes to the
  canary for its share of idempotency-key hashes, else to the stable
  version, and a copy (`shadow_of`) goes to a version in shadow. In shadow
  (`app/runtime/adapters.py`) reads go to the real system and calls that
  change data are recorded, not sent; a checkpoint is approved on the
  spot, and no review task or callback follows. Callers report what really
  happened with `POST /runtime/v1/executions/{id}/outcome`.
  `app/runtime/rollout.py` compares the new version with the previous one:
  paired (agreement, McNemar where the outcome is known) for shadow
  copies, rates only for a canary. `app/runtime/metrics.py` counts per
  deployment over an hour, day or week. `app/runtime/monitoring.py` runs
  after each case: alert rules (in-app, webhook, optional pause), the
  drift check, and the trace export (`app/runtime/telemetry.py`, OTLP/HTTP
  JSON, a `runtime_telemetry` job). `app/runtime/drift.py` freezes the
  twin baseline at deployment and checks live behaviour and inputs
  against it. The Runtime page (`frontend/src/pages/RuntimePage.tsx`,
  `/runtime`) lists deployments with their numbers and drift. For each
  one it shows the comparison, promote and rollback controls, the drift
  findings with "add these cases to a dataset", the cases with the step
  inspector, and alert rules and the collector endpoint (admins).
  Migration `f7a8b9c0d1e2`.
- **Tests.** `tests/test_runtime_rollout.py` (10): a shadow copy looks
  the customer up for real and doesn't open the account, opens no review
  task, and is paired with the live case; promote needs another person;
  25% then 100% retires v1. A first version in shadow takes cases itself
  and is scored against reported outcomes. A canary takes about its share
  by key hash, a repeated key finds its case, and rollback sends new cases
  back while accepted ones finish. A canary needs something live.
  Metrics count statuses, failures and tool calls. An alert rule is
  validated, notifies, calls its webhook and pauses. Traces reach the
  collector with a span per step and no payloads. Drift goes from
  no_baseline to not_enough to flagged (decision mix and an input field),
  pauses, and its cases go to a dataset once. PSI and input profiles. A
  baseline is frozen from a real batch, and absent without one.
  `frontend/src/pages/RuntimePage.test.tsx` (3).
- **Live.** `testing/runtime/smoke_runtime_rollout.py` (29/29 on
  2026-10-17), with the same stand-in, which now also plays the OTLP
  collector and the alert webhook, and with the loan system signing in
  through Keycloak. The first run found that shadow withheld reads too
  (decision log).
- **Deviations:**
  - **Email alerts aren't built** (no mail service): a rule with
    `email` is refused. Owners are always notified in the app, and a
    webhook can reach any paging tool.
  - **Retired lets accepted cases finish.** This was a change to step 2's
    behaviour, where retiring stopped them like a kill. A kill still does.
  - **No live drift on a real baseline yet:** the smoke test's agent was
    approved without a batch, so the live check shows "no baseline". The
    baseline extraction is tested on a real batch in the unit tests, and
    the check on a frozen baseline.
  - **Metrics are computed on request** from executions, steps, reviews
    and the audit log, not pre-aggregated. Fine at pilot volumes; a
    rollup table is the step when a deployment runs thousands of cases a
    day.
  - **Rate-limit state:** alerts and drift checks are throttled by
    timestamps on the deployment (`monitor_state`), not a lock, so two
    workers finishing cases at the same moment may both evaluate and,
    rarely, both fire the same alert.

## US39.16 as built (step 5)

- **Code.** `app/runtime/events.py`: a deployment's event subscriptions
  (`event_subscriptions`, migration `a8b9c0d1e2f3`). The tenant's system
  POSTs to `/runtime/v1/events/{subscription id}`, signing
  `"<timestamp>.<raw body>"` with HMAC-SHA256 (the Stripe/Slack scheme;
  header names and the signature prefix are per subscription). The secret
  is a `secret://tenant/...` reference resolved when the event arrives. A
  timestamp outside five minutes is refused, so a captured event can't be
  replayed. The event id (`event_id_path`) is the idempotency key, and
  `input_mapping` uses Epic 31's dotted paths (`connectors.mapping.value_at`).
  The event then goes through the same `submit` as the API, so routing,
  caps, input checks, canary and shadow all apply. `event_filter` ignores
  other event types with a 200. Subscriptions are managed under
  `/api/processes/{id}/deployments/{id}/event-subscriptions` (`agent.deploy`).
  A subscription must map every input field and nothing else.
- **Tests.** `tests/test_runtime_events.py` (4): a subscription is checked
  before saving (an inline secret, an unknown field, a field left out). A
  signed event starts the agent with the mapped input, trigger `event`, and
  a redelivery is the same case. Forged, tampered, replayed and unsigned
  events get 401 and start nothing. A filtered event, one without an id, an
  unmappable one, a non-object body, an unset secret (503) and a revoked
  subscription (404) are each handled.
- **Live.** `testing/runtime/smoke_runtime_events.py` (19/19 on
  2026-10-17): the loan system's signed `loan.submitted` event ran a real
  case that opened the account; redelivery, another type, a forged, a
  10-minute-old and an unsigned event did not.
- **Not built:** pulling events from a queue (Kafka, SQS) -- only pushed
  webhooks. A signature scheme other than HMAC over timestamp and body
  (e.g. GitHub's, without a timestamp) can't be configured, because then
  replays couldn't be refused.

## US39.18 as built (step 5)

- **Code.** `app/runtime/handoff.py`. An approver hands a pending
  deployment off instead of activating it (`POST .../handoff`, the same
  segregation rule). It becomes `external`: it takes no cases here, needs
  no bindings, and its bundle is fetched again with `GET .../handoff`. The
  bundle (`axyntro.agent-handoff/v1`) holds the A2A agent card (one skill,
  input and output schemas under an `axyntro` extension), MCP tool
  definitions (with `readOnlyHint` and `destructiveHint` from the
  contracts), the definition, tool contracts, guardrails and escalation
  policy, the pinned snapshot, and an evidence summary signed as an EdDSA
  JWS. The summary names the approval, the twin baseline (or why there is
  none) and the SHA-256 of the canonical JSON of everything above. The
  public key is at `/runtime/v1/handoff/keys` (JWKS), so the receiver
  verifies without a shared secret. The receiving platform reports
  executions to `/runtime/v1/deployments/{id}/reports` with a runtime
  client (bearer or OAuth). They're stored as executions of the external
  deployment (trigger `external`, one step per reported tool call, a
  repeated id skipped), so metrics, alerts, drift and the Runtime page
  cover them unchanged. The key is `HANDOFF_SIGNING_KEY_FILE` (Ed25519
  PEM), made on first use outside production.
- **Tests.** `tests/test_runtime_handoff.py` (4): the bundle verifies, has
  the card, MCP tool hints, contracts and guardrails, and fails once
  changed. Fetching again gives the same content hash. Only an approver
  other than the requester hands off. An external deployment refuses
  cases. Reports are deduplicated (also within a batch), validated, need a
  client, and feed metrics, step detail and drift. Only an external
  deployment takes reports.
- **Live.** `testing/runtime/smoke_runtime_handoff.py` (17/17 on
  2026-10-17): as the receiving platform, with only HTTP, PyJWT and its own
  hash, the evidence checked out against the JWKS and a changed bundle
  failed. Three executions reported with a Keycloak client-credentials
  token showed on the Runtime page.
- **Deviations:**
  - The A2A card's `url` is empty: the receiving platform serves the
    agent and fills it in.
  - Locally the key lives in the API container's `.data`, which has no
    volume, so a rebuild makes a new key. A bundle fetched again is
    re-signed with it, but a receiver holding an old bundle must fetch
    again. Production must provide the key file, and key rotation (several
    keys in the JWKS) isn't built.
  - The receiving platform's kill switch is its own: killing an external
    deployment here only refuses its reports.

## User Stories

### A. One executor

US39.1 -- Extract the execution loop from the twin.
As a Platform Engineer, I want the twin and production to run agents
through the same code, so what was tested is what runs.
Acceptance:
- **The move:** `app/runtime/executor.py` holds the loop now in
  `run_scenario`: model turns, the repair turn, `check_tool_permission`,
  `GuardrailTracker`, escalation, suspend and resume, step emission. It
  takes a `ToolPort`, a `HumanPort` and a `StepSink`, and knows nothing
  about scenarios, faults, stubs or grading.
- **The twin wrapper:** `run_scenario` keeps its signature, builds the
  twin ports from today's code (faults, stubs, records, API mode, seeded
  and manual reviewers) and grades the outcome as it does today.
- **Proof:** every existing twin, orchestration, batch and process-twin
  test passes without edits. A replay test runs a seeded batch before and
  after the change and compares the step sequences, which must be
  identical.
- **A guard:** a test fails if `app/runtime` imports anything from
  `app/twin` except the gateway, so twin concerns can't leak back in.

US39.2 -- Deployments.
As an Automation Architect, I want to deploy one approved version of an
agent as a record of what runs, so "deployed" means something can run.
Acceptance:
- **Table:** `agent_deployments` (id, artifact id, artifact version,
  snapshot, environment `production|staging`, status
  `pending|shadow|active|paused|killed|retired`, rollout share, trigger
  settings, identity id, created by, approved by, created, retired).
- **Preconditions:** the artifact version is APPROVED (Epic 23), declares
  at least one tool contract, and every contract has an adapter binding
  (US39.6). Otherwise the deployment is refused with the missing items
  listed, the same way the checklist lists them.
- **Duties:** a new `agent.deploy` permission. Whoever approves the
  deployment may not be whoever built the artifact or requested the
  deployment (Epic 37's segregation rules). Every change of status is an
  audit event.
- **Versions:** regenerating an artifact never changes a running
  deployment. A new version is deployed alongside the old one (US39.13)
  and the old one is retired.
- **Compatibility:** `mark-deployed` is kept for publications to
  external registries, and its UI label becomes "Marked deployed
  elsewhere", so it isn't confused with a running deployment.

US39.3 -- A durable execution store and its own workers.
As a Platform Engineer, I want production executions recorded step by
step and run apart from the API and the twin, so a redeploy or a large
batch can't lose or starve real work.
Acceptance:
- **Tables:** `agent_executions` (deployment, version, status, caller,
  identity, idempotency key, input, final output, cost, tokens, timings)
  and `execution_steps`, appended one row per step and never updated.
  Summary columns on the execution are maintained on write.
- **Workers:** executions run as a `runtime_execution` job kind, on a
  worker pool that claims only production kinds. Twin batches can't take
  its capacity. Fair share across tenants (US38.9) applies.
- **Restarts:** a step with a side effect records "requested" before the
  call and "done" after it. An execution recovered after a crash resumes
  from its last recorded step. It never repeats a mutating call whose
  outcome is unknown: it escalates the execution instead.
- **Retention:** inputs, outputs and tool payloads are kept for a
  tenant-set period (default 90 days), then removed, leaving the step
  metadata. Fields with a data classification marked sensitive (Epic 29)
  are redacted before they're stored.

US39.4 -- Ways to start an agent.
As a Process Owner, I want work to reach a deployed agent from our own
systems, so it runs on real cases without someone pressing Run.
Acceptance:
- **API:** `POST /runtime/deployments/{id}/executions` with the agent's
  input schema, called by a tenant API client (client credentials, not a
  user session). It needs an `Idempotency-Key`, and the same key returns
  the same execution. It answers 202 with an execution id; a status
  endpoint and an optional callback URL report completion.
- **Validation:** input that doesn't match the input schema is refused
  with 422, before any model call.
- **Queue:** the same call can be made through the job queue for
  internal triggers, such as a scheduled sweep.
- **Not in this story:** event subscriptions (webhooks from the
  customer's systems) and whole-process execution, which are US39.16 and
  US39.17.

### B. Identity and credentials

US39.5 -- A workload identity per deployment.
As a Security Officer, I want every agent to act as a named identity of
its own, so each action can be traced to the agent and the caller it
acted for, and its access can be revoked on its own.
Acceptance:
- **Table:** `agent_identities` (id, tenant, deployment, display name,
  status, created, revoked). It isn't a row in `users`, has no password
  or session, and can't hold UI roles.
- **Attribution:** every execution step and audit event records the
  identity, and the caller (API client or user) it acts on behalf of.
- **Revocation:** revoking an identity stops its deployment (same effect
  as a kill, US39.11) and invalidates its cached tokens.
- **Directory:** optionally, an identity maps to a service principal in
  the customer's IdP (a client id), so their own logs name the same
  agent. A secret reference is held, never the value.

US39.6 -- Production adapters for tool contracts.
As an Automation Architect, I want each tool contract bound to a real
system's operation, so the agent calls the system the twin simulated, in
the shape it was tested against.
Acceptance:
- **Binding:** `adapter_bindings` link a deployment's tool contract to an
  application endpoint tagged `production` (Epic 29), an allow-list of
  its operations, and a credential (US39.7). The binding's operations
  must match the tool schema the twin used. A difference is shown, and
  blocks the deployment until it's accepted.
- **The endpoint check, reversed:** production adapters may call only
  endpoints tagged `production` and registered by a catalog editor. The
  twin's API mode keeps refusing them, so a twin run can never reach a
  production system.
- **Calls:** each call obeys the contract's `timeout_seconds` and
  `retry_max_attempts`. Mutating calls carry the execution's idempotency
  key plus the step number, so a retry can't apply twice. Redirects
  aren't followed.
- **Responses:** large or non-JSON responses are cut down the same way
  the twin does it, so the model sees the same shape it was tested with.

US39.7 -- Scoped, short-lived credentials.
As a Security Officer, I want agents to hold no long-lived secrets and
only the access their tool contracts need, so a leaked token is worth
little and an agent can't exceed its permissions in the target system.
Acceptance:
- **Kinds:** OAuth 2.0 client credentials (the identity's client id and a
  secret reference; token fetched per call when expired, cached in memory
  only, never stored or logged) and, where a system has nothing better,
  a static API key held as a secret reference.
- **Scopes:** the token request asks only for the scopes mapped to the
  contract's (resource, action) in the binding. A token for a read
  contract can't be used for a mutating one.
- **Checks:** a deployment can't go active while any binding's credential
  is unresolved. A "test credential" action fetches a token without
  calling the system.
- **Never in prompts:** credentials are attached by the adapter after
  the Tool Gateway has allowed the call. They never enter the model's
  messages, the step record or the logs, and a test asserts that.

### C. Guardrails in production

US39.8 -- The same guardrails, failing closed.
As a Compliance Lead, I want production to enforce the limits and
permissions the agent was tested with, with no fallback to "allow", so
the release evidence still holds.
Acceptance:
- **Same checks:** `check_tool_permission` and `GuardrailTracker` run
  unchanged, from the snapshot pinned on the deployment, not from the
  artifact's current (possibly edited) definition.
- **Same instructions:** production sends the run instructions the twin
  sent (which tools map to which systems, how to ask for review, the
  output keys), without the line calling it a test run.
- **No fail-open:** production refuses an artifact with no tool contracts
  (US39.2) and refuses any tool call without a binding.
- **Outcomes:** a denied call, an exceeded limit or a timeout ends the
  execution as the twin would (FAILED, ESCALATED, TIMED_OUT), and an
  escalation creates an inbox task (US39.10).
- **Proof:** a parity test runs the same artifact with the same scripted
  model responses through the twin ports and the production ports, and
  asserts the same policy decisions and terminal status.

US39.9 -- Limits across executions.
As a Process Owner, I want caps on what an agent does in total, not only
per run, so a runaway trigger or a broken system can't run up cost or
damage.
Acceptance:
- **Per deployment:** executions per minute, concurrent executions,
  spend per day, and mutating calls per hour. Defaults come from the
  twin's measured volume and cost (Epic 35) with headroom, and are
  editable by an admin.
- **When a cap is hit:** new executions are refused with 429 and a
  reason. Running ones finish. The tenant's own limits (US38.9) still
  apply on top.
- **Circuit breaker per tool:** after N consecutive failures (default 5)
  or an error rate over a threshold in a window, the tool is opened. An
  execution that needs it escalates instead of calling it, and a probe
  call after a cool-down closes it again.
- **Argument rules (optional per contract):** simple bounds on call
  arguments, such as "amount ≤ 10,000" or "currency in [EUR, USD]",
  checked by the gateway. A breach is denied like a missing permission.

### D. Humans in the loop

US39.10 -- A review inbox.
As an Approver named in an agent's escalation policy, I want the
checkpoints and escalations for live cases in one inbox, so real cases
don't wait on someone noticing.
Acceptance:
- **Tasks:** a `review_tasks` table. Each task has the execution, the
  proposed action, the agent's reasoning summary, the case input, an
  assignee (a role or business unit from the approval matrix, Epic 30, or
  the policy's `escalation_target.role`), a due time and a status.
- **Deciding:** approve or reject, with a required comment on reject.
  This resumes the execution through `HumanPort` exactly as a manual twin
  checkpoint resumes. The decision and the person are recorded on the
  step and in the audit log.
- **Escalated executions:** a reviewer can resolve the case by hand and
  record the outcome, and the execution closes as ESCALATED with that
  outcome attached.
- **SLAs:** a task past its due time is escalated to the next level in
  the matrix and the deployment's owners are notified.
- **Duties:** nobody can approve a task for an execution they triggered.
- **Feedback:** each decision is kept as a labelled outcome for
  monitoring (US39.15) and can be sent to a dataset (Epic 32).

### E. Stopping and rolling out

US39.11 -- The kill switch.
As a Process Owner or Security Officer, I want to stop an agent, or all
our agents, at once, so a misbehaving agent does no further harm while we
find out why.
Acceptance:
- **Levels:** one deployment, every deployment of one agent, or every
  deployment in the tenant. Each is one action with a required reason.
- **Who:** the deployment's owners, any approver or admin in the tenant,
  and the platform operator for a whole tenant during an incident. An
  operator kill is recorded in both the tenant's and the platform audit
  log, and gives the operator no data access.
- **Effect on new work:** refused at the front door with 503 and the
  reason, within 5 seconds of the action, on every API and worker
  instance.
- **Effect on running work:** each execution stops at its next step
  boundary (before the next model call or tool call), with status
  KILLED. A tool call already in flight is allowed to finish and is
  recorded. It isn't retried or followed by another. Open review tasks
  are cancelled.
- **No dependencies:** the check reads the database (or a cache that is
  never more than 5 seconds old) and needs neither the model provider nor
  the target systems to be reachable.
- **Restart:** a killed deployment comes back only through a new
  activation, approved like a deployment, never by undoing the kill.
- **Proof:** a test kills a deployment with 20 executions in flight and
  asserts none takes another step and new calls get 503.

US39.12 -- Automatic pause.
As a Process Owner, I want an agent paused automatically when it clearly
goes wrong, so nobody has to be watching at 3 a.m.
Acceptance:
- **Triggers (each configurable, with defaults):** policy denials over a
  rate, error rate over a rate, spend in an hour over a multiple of the
  baseline, reject rate in the inbox over a threshold, or a drift alert
  marked "pause" (US39.15).
- **Effect:** status `paused`: new work is refused or queued (the
  deployment chooses which), running executions finish. Owners are
  notified with the trigger and the numbers behind it.
- **Resume:** an owner resumes it with a comment. A paused deployment
  isn't killed and needs no new approval.

US39.13 -- Shadow, canary and rollback.
As an Automation Architect, I want a new version to prove itself on live
cases before it carries them, so the step from twin to production is
gradual and reversible.
Acceptance:
- **Shadow:** a deployment in `shadow` receives a copy of live work. Read
  calls go to the real system. Mutating calls are recorded with their
  arguments and are not sent. The output is stored next to what actually
  happened (the active version's output, or the human outcome supplied
  by the caller). Required before `active` for any agent with a mutating
  tool, unless the approver waives it with a reason.
- **Canary:** an `active` new version takes a set share of executions
  (for example 5%, then 25%, then 100%). The split is by a hash of the
  idempotency key, so a case always goes to the same version.
- **Promotion:** comparison with the current version (decision mix,
  escalation rate, error rate, cost) uses Epic 35's paired comparison
  where cases overlap, and is shown before each step up.
- **Rollback:** one action sends all new work back to the previous
  version and retires the canary. Running executions finish on the
  version they started on.

### F. Monitoring and drift

US39.14 -- Live metrics and traces.
As a Process Owner, I want to see how each agent is doing right now, so
problems show before customers report them.
Acceptance:
- **Metrics per deployment:** executions by status, latency (p50, p95),
  cost and tokens, tool calls and errors by tool, policy denials,
  escalations, inbox approve and reject rates, time waiting for humans,
  and kill or pause events.
- **Page:** a Runtime page lists deployments with their status and these
  numbers over the last hour, day and week. Each execution opens in the
  existing step inspector (Epic 18), which reads `execution_steps` the
  same way it reads twin steps.
- **Export:** OpenTelemetry traces (one span per step, with the tenant,
  deployment, version and identity as attributes, payloads excluded) and
  metrics, to an endpoint the deployment's operator configures.
- **Alerts:** threshold rules on any metric send to a webhook or email.
  An alert names the deployment and links to it.

US39.15 -- Drift against the twin baseline.
As a Compliance Lead, I want to know when an agent behaves differently
live from how it behaved in the twin, so the release evidence isn't
silently out of date.
Acceptance:
- **Baseline:** at deployment, the twin results the version was approved
  on (decision mix, escalation rate, tool-call mix, cost and latency
  distribution, pass rate with its confidence range) are frozen on the
  deployment. A version approved without batch evidence has no baseline,
  and the page says so.
- **Live comparison:** over rolling windows (default 7 days, at least 100
  executions), the same numbers are computed live. A change is flagged
  when it's outside the baseline's range with stated confidence (Epic 35
  methods; population stability index for the decision mix).
- **Accuracy:** where live outcomes are known (inbox decisions, shadow
  comparisons, outcomes the caller reports back), live accuracy is shown
  with a confidence range next to the twin's.
- **Input drift:** the distribution of input fields compared with the
  dataset the twin used, so "the cases changed" can be told apart from
  "the agent changed".
- **Action:** each drift alert is marked notify or pause (US39.12). A
  flagged window offers "add these cases to a dataset", so the twin
  learns from what production saw (Epic 32).

### G. Later

US39.16 -- Event triggers.
As a Process Owner, I want an agent started by an event in our systems,
so we don't have to build a caller.
Acceptance: signed webhooks per deployment (HMAC over the body with a
secret reference, replay window, idempotency from the event id), mapped
onto the input schema with Epic 31's field mapping.

US39.17 -- Whole-process execution.
As a Process Owner, I want a case to move through all of a process's
deployed agents along the real flow, so the to-be process runs end to
end, not one agent at a time.
Acceptance: the orchestrator's walk (`run_orchestration`) runs on the
production ports, with the case file hand-off and gateways from Epic 36.
A node without an active deployment becomes a human task in the inbox.
Each case is one process execution with its node executions under it.
Kill and pause at process level stop every node.

US39.18 -- Certified hand-off to the customer's agent platform.
As an Automation Architect at a customer with its own agent platform, I
want to export an approved agent with its evidence, so we can run it
there and still trust the twin results.
Acceptance: an export in MCP tool and A2A agent-card form with the
definition, tool contracts, guardrails, the snapshot and a signed
evidence summary. The receiving platform may report execution metrics
back to the drift view (US39.15) through the same metrics API.

## Build order

1. **US39.1:** extract the executor. No behaviour change. Done when the
   twin suite and the replay test pass. Worth doing on its own, because
   it also shrinks `engine.py`.
2. **US39.2-39.8, US39.11:** deployments, the execution store and
   workers, the API trigger, identity, adapters, credentials, guardrails
   failing closed, and the kill switch. The kill switch ships in the same
   step as the first real execution, never after it. Done when a twin
   tested agent runs against a local "production" stand-in with OAuth,
   and the kill test passes.
3. **US39.10, US39.9, US39.12:** the inbox, limits across executions,
   automatic pause. Needed before an agent with a mutating tool or a
   human checkpoint goes live.
4. **US39.13-39.15:** shadow, canary and rollback, live metrics, drift.
   Needed before the first pilot runs unattended.
5. **US39.16-39.18:** when a customer asks.

## Testing

- **Parity (US39.1, US39.8):** seeded replays give identical steps before
  and after the extraction. The same scripted model responses through twin
  and production ports give the same policy decisions and outcomes.
- **Fail closed:** no contracts, no binding, an unresolved credential, a
  non-APPROVED version, a revoked identity, a killed deployment: each is
  refused, and a test covers each one.
- **Kill (US39.11):** 20 in-flight executions stop at their next step,
  new calls get 503 within 5 seconds, with the model provider stubbed to
  hang.
- **Crash (US39.3):** kill the worker between "requested" and "done" on a
  mutating call. The recovered execution escalates rather than calling
  again.
- **Secrets:** no token or secret value appears in steps, logs, audit
  events or model messages (a scan over everything a test run wrote).
- **Tenancy:** the cross-tenant probe (US38.5) covers the new routes, and
  an API client of one tenant can't start another tenant's deployment.
- **Live, per the project's "run it against something real" rule:** a
  small "production" stand-in service that accepts client-credentials
  tokens from Keycloak (in the local SSO stack since Epic 37). The Invoice to Pay demo's
  agents are deployed against it: shadow first, then a canary, then
  killed mid-batch. Defects found there go to the decision log.

## Notes / Open Questions

- **Decided (confirmed 2026-10-17): run agents ourselves first, hand off to customer
  platforms later (US39.18).** Running them here keeps the executor shared
  with the twin, which is the point of the product: tested is what runs,
  and drift can be measured. A hand-off first would be quicker to ship but
  leaves both guarantees to someone else's platform.
- **Decided (confirmed 2026-10-17): shadow mode is required before `active` for agents with a
  mutating tool,** waivable by the approver with a reason.
- **Decided (confirmed 2026-10-17): a kill lets an in-flight tool call finish.** Aborting a
  call mid-flight leaves its outcome unknown, which is worse than one
  more completed call. Nothing runs after it.
- **Decided (confirmed 2026-10-17): the platform operator may kill a whole tenant's agents**
  during an incident, without data access, logged in both audit logs.
- **Decided (confirmed 2026-10-17): credentials are OAuth client credentials or static keys
  by secret reference.** Workload identity federation (a cloud-issued
  identity exchanged for a token, no secret at all) is better and needs
  a cloud deployment first. The `agent_identities` table leaves room for
  it.
- **Prerequisites from the Critical Review:** a worker deployment separate
  from the API (item 2) and a secrets manager (item 3). The local
  `EnvSecretStore`/`FileSecretStore` is enough to build and test this
  epic. It isn't enough to run a customer's production credentials.
- **Out of scope:** billing for executions (usage records exist), an
  agent marketplace, agents that write or change their own tools, and
  computer-use or browser tools. Agents call systems only through
  declared tool contracts.
