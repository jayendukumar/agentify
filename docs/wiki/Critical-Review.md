# Critical review of the architecture

Last reviewed: 2026-10-15 · Sources: `backend/app`, `frontend/src`, `docker-compose.yml`, `docs/code-health/report.json`

This is a candid assessment of where Axyntro is weak, written to decide
what to fix before it's offered to enterprise customers. It's grounded in
the code and in measurements ([Code Health](Code-Health.md)), not
impressions. The companion deck
(`demo-deck/axyntro-enterprise-readiness.html`) turns it into a plan.

## Verdict in one paragraph

The product idea and the domain model are strong, ahead of most tools in
this space. Process capture, explainable blueprints, governed agent
definitions, a reproducible twin, ground truth at volume, statistical
release gates, and now the whole-process twin form one coherent line from
document to evidence. The *platform* under it is a well-tested single-tenant
prototype: one process with roles and segregation of duties but no
single sign-on yet, a database used as a
document store, a 2,400-line repository module doing orchestration, an
in-process job worker, one LLM provider, and nothing that runs an agent in
production. Most weaknesses below are platform weaknesses, which are
fixable without rethinking the product.

## Strengths worth keeping

- **Deterministic rendering around probabilistic judgement.** BPMN XML,
  layouts and agent definitions are rendered by code from LLM decisions,
  so they're valid by construction and unit-testable.
- **Evidence over opinion.** Twin grading compares traces and labelled
  outcomes. LLM judges are optional, need calibration, and apply only to
  free text. Rates carry Wilson intervals, and version comparisons use
  McNemar's test.
- **Reproducibility.** Runs snapshot every version that influenced them,
  and batch cases are seeded.
- **Production safety in the twin.** API mode is deny-by-default and
  re-checks every call. Sample-data systems (Epic 36) remove the need to
  touch real systems at all.
- **Test discipline.** About 520 backend tests and a typed frontend
  client. Defects found live are written up in `planning/decision-log.md`.

## Weaknesses, by severity

### Critical: blocks any enterprise deployment

1. **There is still no real authentication.** `POST /api/auth/login`
   accepts a name and *self-chosen* roles, so anyone who can reach the API
   can become an Approver or Admin. Epic 37 has built everything around
   sign-in, but not sign-in itself:
   - roles and permissions, scoped by business unit;
   - segregation of duties and an append-only audit log;
   - short, rotating, revocable sessions with CSRF protection;
   - a startup refusal of local sign-in in production.

   SSO (OIDC/SAML), MFA verification and SCIM provisioning are steps 3-5.
   Until they land, the product can't be deployed for real users.
   (`api/auth.py`, `auth/`, Epic 37)
2. **It's single-tenant by construction.** The organization is a
   singleton, and no table carries a tenant id. Serving two customers
   means two deployments, and nothing stops cross-customer data mixing if
   someone tries otherwise. (`organizations`, every `process_id` tree)
3. **Agents never run in production.** "DEPLOYED" means "published to a
   registry entry". There's no agent runtime, no production tool gateway,
   no identity for an agent to act with, no kill switch, and no production
   monitoring. The twin is the only place an agent executes. The value
   chain stops at evidence. (Epics 15, 23)
4. **Secrets and data handling are local-grade.** API keys are in
   `backend/.env`. Uploaded documents sit unencrypted on the container
   filesystem (`.data/uploads`), and so do the usage log and app log.
   Policy text, process documents and twin case data are sent to a
   third-party model gateway (OpenRouter) by default. Per-unit routing and
   redaction exist (Epic 31), but redaction is regex-based and nothing is
   default-deny.

### High: limits scale, reliability or trust

5. **The job worker lives inside the API process.** A long batch competes
   with request handling. A redeploy kills in-flight agent turns: leases
   recover the jobs, but a half-run case restarts, its cost is paid twice,
   and a partial trace can be left behind. Concurrency is a single number
   per kind. There's no fair-share between tenants or processes, and no
   back-pressure from the LLM provider beyond a semaphore.
6. **Run traces are rewritten on every step.** `on_step` does
   `run_model.steps = [*run_model.steps, step]` and commits. That makes each
   run O(n^2) bytes written, and one hot row per run. A 9-agent process
   twin case produces about 150 steps. At 1,000 cases with 8 workers, this
   becomes the write bottleneck and bloats the table and WAL.
   (`db/repository.py`, `simulation_runs.steps`)
7. **Derived state is recomputed on read.** An agent's lifecycle status
   scans *all* its runs on every read (`_compute_agent_lifecycle_status`).
   Batch results, gate checks and twin summaries also aggregate over raw
   run rows. That's fine at hundreds of runs, but slow at the hundreds of
   thousands volume testing is meant to produce.
8. **The database is used as a document store.** 85 of 506 columns are
   JSON, including the agent definition, run steps, scenario config,
   blueprint nodes and reports. The real schema lives in Pydantic. That
   means no constraints, no indexes into payloads, and migrations that
   can't transform data shapes. Old rows can only fail validation at
   read time.
9. **The orchestration core is large and fragile.** `run_orchestration`
   (cognitive complexity 77, ~460 lines) and `run_scenario` (66) carry
   resume, suspend, cancel, routing, case file and grading in nested
   closures. Mutation testing kills only 56% of injected bugs in
   `twin/orchestrator.py`. The process twin made it more capable, but not
   simpler.
10. **`db/repository.py` is a god module.** About 2,400 lines, 41 outgoing
    and 26 incoming dependencies, and the top hotspot (19 of 41 commits).
    It mixes persistence with orchestration (`execute_*_background`). Most
    feature work touches it, so it's where merge conflicts and regressions
    concentrate.
11. **Dependency cycles are hidden, not removed.** There are no
    import-time cycles, but 47 function-level imports exist to dodge them.
    Counted, they form one cycle spanning 18 backend packages
    (jobs -> main -> routers -> services -> jobs). It works, but no package
    can be extracted or tested in isolation, and import order bugs surface
    at run time.
12. **Twin fidelity has limits.** The twin has no parallel gateways (forks
    fail the run), no timers or message events, no queueing or capacity
    model (waits are sampled, not simulated, see US36.9), and no
    multi-instance tasks. Proxy-mode systems are invented by an LLM. Sample
    data (Epic 36) fixes the world's consistency but not its behaviour:
    writes don't trigger downstream effects.
13. **Policy extraction isn't repeatable.** Seeding the Epic 36 demo three
    times extracted 31, 13 and 9 rules from the same two-page AP policy.
    Everything downstream depends on that output: context packs, blueprint
    citations, compliance coverage. A person confirms the proposals, but
    there's no recall check against a reference extraction and no
    consistency run, so a missed rule is only noticed if someone knows to
    look for it. *(Measure extraction like an agent: a labelled policy set,
    and repeat runs that compare what was found.)*
14. **It depends on one LLM provider and a small model.** The default is
    one cost-optimised model through one gateway, with no evaluation-based
    model selection per operation and no fallback provider. The Epic 27-35
    demo agent scored about 53% on its golden set. The platform measured
    that honestly, but the generated agents themselves need stronger
    models or better prompts for many tasks.

### Medium: slows the team down

15. **Frontend components are monolithic.** `BlueprintPage` (cognitive
    91), `DigitalTwinAdvancedPanel` (93) and `TwinTestBuilder` (549 lines)
    hold data loading, polling, forms and rendering in one function. There's
    no query cache, so every page polls on its own with `setInterval`.
16. **The API layer repeats itself.** The twin, orchestration and
    evaluation routers are structural copies (3.9% renamed duplication in
    the backend, concentrated there and in the repository's load/convert
    blocks).
17. **Configuration is environment variables only.** Thresholds that are
    really business settings (guardrails, budgets, tolerances) are spread
    across `config.py`, agent definitions and scenario JSON, with no admin
    UI or audit of changes.
18. **Observability is logs and polling.** There's no metrics endpoint, no
    tracing across API -> job -> LLM, no alerting on job backlog, cost or
    error rate.
19. **There's no CI.** Tests, type checks, the code-health ratchet and the
    docs check all exist but only run when someone runs them. The backend
    has no linter or type checker configured.
20. **The tests mock the seams where defects live.** Building the Epic 36
    demo surfaced five defects that the ~600 unit tests couldn't catch, all
    found only against the real model:
    - a blueprint truncated at the output-token cap
    - one malformed JSON reply failing a whole generation
    - a 1,000-token cap that returned nothing
    - agent steps dropped from orchestration traces
    - an unready agent on an end event blocking every run

    The tests replace `LLMClient` and `run_scenario` wholesale, so callbacks,
    token limits and real output shapes go unexercised. There's no recorded
    LLM-fixture suite and no nightly live smoke run. *(Add a small live
    smoke suite (seeded, budget-capped) and recorded-response fixtures for
    every operation.)*
21. **Documents are re-extracted, not versioned by content.** There's no
    deduplication or content hashing on upload, so the same policy uploaded
    twice is processed twice.

### Product weaknesses (not code)

- **Value is hard to show before the twin.** Up to the process twin,
  outputs are diagrams and percentages. Epic 36's operating model and
  efficiency report are the first business-case numbers. They depend on
  the customer supplying honest time and motion data.
- **Generated agents are specifications, not products.** A customer still
  needs an engineering team and a runtime to deploy them (weakness 3).
- **Who owns what is clearer, but the screens aren't.** Epic 37's six
  roles match the work, and the server enforces them per business unit.
  The UI still shows the same screens to everyone, and only enables or
  disables actions. A scoped user sees enabled buttons on another unit's
  process and gets a 403 with a reason.

## What to do first

The order is in the enterprise-readiness deck. In short:

1. **Identity and tenancy:** OIDC SSO with MFA, SCIM provisioning (roles,
   segregation of duties, audit and session hardening are done), tenant id
   everywhere, row-level security. *(Weaknesses 1, 2)*
2. **Separate the worker, and make runs append-only:** a worker deployment
   on the same queue, a `run_steps` table appended per step, and summary
   columns maintained on write. *(5, 6, 7)*
3. **Security baseline:** a secrets manager, object storage with
   encryption, KMS, default-deny model policy, DLP-grade redaction, and
   tamper-evident export of the audit log. *(4)*
4. **Agent runtime:** a production executor reusing the twin's tool
   gateway and guardrails, agent identities, kill switch, monitoring, and
   drift detection against the twin baseline. *(3)*
5. **Refactor the hot core** behind the ratchet: split `repository.py`
   into per-domain stores plus services, extract the orchestrator's walk
   into a small state machine, remove lazy-import cycles. *(9, 10, 11)*
6. **CI** running tests, `tools.code_health check` and `tools.docs check`
   on every change. *(19)*
