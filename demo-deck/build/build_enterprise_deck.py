"""Builds the enterprise-readiness deck: what it will take to take Axyntro
from a well-tested single-tenant build to enterprise grade.

Facts come from the code-health report (docs/code-health/report.json) and
the critical review (docs/wiki/Critical-Review.md); effort figures are
estimates and labelled as such.

    python build_enterprise_deck.py ../axyntro-enterprise-readiness.html ../axyntro-enterprise-readiness.pptx
"""

import json
import sys
from pathlib import Path

from deck_kit import build, cards, compare, kpis, roadmap, section, statement, table, title

REPO = Path(__file__).resolve().parents[2]
health = json.loads((REPO / "docs" / "code-health" / "report.json").read_text(encoding="utf-8"))
cx, size = health["complexity"], health["size"]
stories = sum(1 for p in (REPO / "planning" / "epics").glob("*.md")
              for line in p.read_text(encoding="utf-8").splitlines() if line.startswith("US") and " -- " in line)
mutation = {Path(k).name: v["score"] for k, v in health["mutation"].items() if v.get("score") is not None}
per_story = [round(g["prod_loc"] / g["stories_implemented"]) for g in (health["growth"][0], health["growth"][-1])]

slides = [
    title("Axyntro · Enterprise readiness", "What it will take to make Axyntro enterprise grade",
          "An honest assessment of the build after 36 epics, the gaps that matter to an enterprise buyer, and a "
          "12-month plan to close them.",
          "Arrow keys or click to move · F for full screen"),

    kpis("Where we are", "A broad product, built fast and tested", "", [
        ("Epics / stories", f"36 / {stories}", "from document ingestion to a whole-process digital twin"),
        ("Production code", f"{(size['backend_prod_loc'] + size['frontend_prod_loc']) / 1000:.0f}k lines",
         f"{size['backend_prod_loc'] / 1000:.0f}k Python, {size['frontend_prod_loc'] / 1000:.0f}k TypeScript"),
        ("Automated tests", "~600", f"{size['backend_test_loc'] / 1000:.1f}k lines backend, {size['frontend_test_loc'] / 1000:.1f}k frontend"),
        ("API endpoints", "177", "49 database tables, 15 model call sites"),
    ], note="Built in about four weeks. Measured with tools/code_health on 2026-10-13."),

    cards("Strengths to keep", "What is already enterprise-strength", "These are design choices a buyer's architects will like:", [
        ("Evidence, not opinion", "Twin runs are graded on traces and labelled outcomes. Pass rates carry confidence ranges, and versions are compared with paired tests."),
        ("Reproducible", "Every run snapshots the agent, environment, dataset, model and policy versions. Batch cases are seeded and replayable."),
        ("Governed agents", "Permissions, tool contracts, guardrails, escalation and model policy are part of the agent definition, and the twin's tool gateway enforces them."),
        ("Context-aware", "The organization's policies, roles and approval limits ground every blueprint and agent, with citations."),
        ("Safe by default in test", "API mode only calls non-production endpoints. Sample-data systems need no live systems at all."),
        ("Whole-process proof", "The process twin runs every agent over many cases and prices the result against today's operating model."),
    ]),

    cards("The bar", "What an enterprise buyer will ask for", "Eight areas, roughly in the order a security review and a pilot will probe them:", [
        ("Identity and access", "SSO, MFA, SCIM, fine-grained roles, segregation of duties between who builds, tests and approves an agent."),
        ("Tenancy and data", "Hard isolation between customers and units, encryption with customer-managed keys, residency, retention."),
        ("Security and compliance", "SOC 2 Type II, ISO 27001, GDPR, EU AI Act evidence; in banking also DORA and outsourcing rules."),
        ("Reliability and scale", "SLA, DR, horizontal scale, thousands of concurrent twin cases, predictable cost."),
        ("Operability", "Metrics, tracing, alerting, audit logs, admin consoles, zero-downtime deploys."),
        ("Production agent runtime", "Agents that actually run, with identity, kill switch, monitoring and drift checks against the twin."),
    ]),

    table("Gap assessment", "Today vs the enterprise bar", "", ["Area", "Today", "Enterprise bar"], [
        ["Authentication", "Name and a self-chosen role at login; no password", "OIDC/SAML SSO, MFA, SCIM, session policies"],
        ["Tenancy", "One organization per deployment, no tenant id", "Tenant id everywhere, row-level security, per-tenant keys"],
        ["Agent runtime", "Agents run only in the twin; 'deployed' = registry entry", "Production executor with identity, guardrails, kill switch, monitoring"],
        ["Secrets and data at rest", "backend/.env; files on the container disk", "Secrets manager, object storage with KMS, CMK, retention"],
        ["Model access", "One provider by default (OpenRouter); regex PII redaction", "LLM gateway, 2+ providers with failover, DLP-grade redaction, default-deny"],
        ["Background work", "Worker inside the API process", "Separate autoscaled worker pool, fair-share per tenant"],
        ["Run storage", "Step trace rewritten on every step (O(n²))", "Append-only step store, summaries maintained on write"],
        ["Observability", "Log files and UI polling", "OpenTelemetry metrics/traces, alerts, cost dashboards"],
        ["Delivery", "docker compose; no CI", "CI/CD with quality gates, IaC, blue/green, DR drills"],
        ["Code health", f"{cx['backend']['over_cognitive_limit'] + cx['frontend']['over_cognitive_limit']} over-complex functions; 2,000-line repository module", "Ratchet enforced in CI; hot core refactored"],
    ], tone=["bad", "bad", "bad", "bad", "warn", "warn", "warn", "warn", "warn", ""]),

    section("The critical four", "Four gaps block any enterprise deployment",
            "Identity, tenancy, a production runtime, and data protection. Everything else can follow a pilot."),

    compare("1 · Identity", "From 'type your name' to real identity and roles",
            "Today", [
                "Login takes a name and a role the user picks: anyone who reaches the API can be an Editor",
                "Two roles: viewer and editor",
                "30-day cookie sessions; no revocation beyond logout",
                "No link to the customer's directory",
            ],
            "Enterprise grade", [
                "OIDC/SAML SSO with the customer's IdP; MFA enforced there",
                "SCIM provisioning; groups mapped to roles",
                "Roles that match the work: analyst, architect, process owner, approver, auditor, admin",
                "Segregation of duties: whoever builds an agent can't approve its release",
                "Short sessions, refresh tokens, admin revocation",
            ], note="Effort: about 6-8 engineer-weeks, including the role model and UI gating."),

    compare("2 · Tenancy", "From one organization to many, isolated",
            "Today", [
                "The organization is a singleton; no table carries a tenant id",
                "A second customer means a second deployment",
                "Business-unit stewardship exists, but only within one organization",
            ],
            "Enterprise grade", [
                "tenant_id on every row, enforced by Postgres row-level security, not just by queries",
                "Per-tenant encryption keys, model policy, budgets and rate limits",
                "Optional dedicated deployments (single-tenant, in-region) from the same build",
                "Tenant-scoped exports and deletion, for GDPR and contract exit",
            ], note="Effort: about 8-10 engineer-weeks. Touches all 49 tables and every repository query, so do it before more features land."),

    compare("3 · Production agent runtime", "From proven in the twin to running for real",
            "Today", [
                "The twin is the only place an agent runs",
                "'DEPLOYED' means published to a registry entry",
                "No agent identity, credentials or production tool adapters",
                "No production monitoring, so no way to compare live behaviour with the twin",
            ],
            "Enterprise grade", [
                "A runtime that reuses the twin's tool gateway, guardrails and checkpoint model, so tested is what runs",
                "Workload identity per agent, with scoped, short-lived credentials to each system",
                "A human-review inbox for real checkpoints, with SLAs and escalation",
                "Kill switch, canary rollout, live metrics, and drift alerts against the twin baseline",
                "Or a certified hand-off to the customer's agent platform (MCP / A2A)",
            ], note="Effort: the largest single item, about 4-6 months for a team of 3-4. It's where customer value is realised."),

    compare("4 · Data protection", "From local files to a defensible data posture",
            "Today", [
                "API keys in backend/.env",
                "Uploads, usage and app logs unencrypted on the container disk",
                "Policy text and case data go to a third-party model gateway by default",
                "PII redaction by regular expression",
            ],
            "Enterprise grade", [
                "Secrets manager (Vault, Key Vault, Secrets Manager); no secrets in images or env files",
                "Object storage with KMS and customer-managed keys; retention policies",
                "Default-deny model policy: EU-hosted or private models unless allowed",
                "DLP-grade redaction or tokenisation before any model call; full model-call audit log",
                "DPIA and records of processing ready for customers' data protection officers",
            ], note="Effort: about 6-8 engineer-weeks, plus the policy work for the DPIA."),

    section("Platform and engineering", "Scale, reliability and the codebase",
            "What changes so the same product serves thousands of cases and dozens of teams."),

    table("Target architecture", "From one container to a platform", "", ["Layer", "Today", "Target"], [
        ["Edge", "nginx serving the SPA; API on :8000", "API gateway / WAF, TLS everywhere, rate limits per tenant"],
        ["API", "One FastAPI process", "Stateless API pods behind a load balancer, horizontal autoscale"],
        ["Work", "Job worker inside the API process", "Separate worker pool on the same queue (or Temporal), autoscaled on queue depth"],
        ["Data", "One Postgres + pgvector in a Docker volume", "Managed Postgres with HA, PITR, read replica, RLS; append-only run store"],
        ["Files", "Container filesystem", "Object storage (S3 / Blob) with KMS"],
        ["Models", "OpenRouter client with per-unit policy", "LLM gateway: multi-provider, failover, eval-based routing, budgets, caching"],
        ["Agents", "Twin only", "Agent runtime + tool gateway + agent identity"],
        ["Ops", "Log files", "OpenTelemetry, metrics, alerting, audit log, admin console"],
        ["Delivery", "start.ps1, docker compose", "Terraform + Helm, CI/CD, blue/green, DR runbooks"],
    ]),

    table("Scale and reliability targets", "What 'it scales' has to mean in numbers", "", ["Measure", "Today", "Target"], [
        ["Concurrent twin cases", "2 (8 in demo mode), one process", "500+ across tenants, with fair share"],
        ["Run trace writes", "Whole trace rewritten per step", "One append per step"],
        ["Agent lifecycle read", "Scans every run of the agent", "Maintained on write, O(1) read"],
        ["Availability", "Single host, no SLA", "99.9% (multi-AZ), 99.95% for the agent runtime"],
        ["Recovery", "None beyond the Docker volume", "RPO 15 min, RTO 1 h, tested quarterly"],
        ["Model provider outage", "Calls fail", "Automatic failover to a second provider"],
        ["Cost control", "Per-batch budget caps", "Per-tenant budgets, alerts and showback"],
    ]),

    kpis("Engineering quality", "Measured, and now gated", "tools/code_health measures complexity, duplication, hotspots, coupling, dead code, growth and mutation score. A ratchet stops any of it getting worse.", [
        ("Over-complex functions", str(cx["backend"]["over_cognitive_limit"] + cx["frontend"]["over_cognitive_limit"]),
         f"cognitive > 15, of {cx['backend']['functions'] + cx['frontend']['functions']}; worst {cx['max_cognitive'] if 'max_cognitive' in cx else cx['backend']['max_cognitive']}"),
        ("Top hotspot", "repository.py", f"{health['hotspots'][0]['loc']:,} lines, changed in {health['hotspots'][0]['commits']} of 41 commits"),
        ("Hidden import cycle", f"{len(health['cycles']['backend_packages_incl_deferred'][0])} packages",
         f"dodged by {health['cycles']['deferred_import_count']} lazy imports"),
        ("Mutation score", f"{min(mutation.values()):.0%}-{max(mutation.values()):.0%}", "share of injected bugs the tests catch, on 4 core modules"),
    ], note=f"Duplication is low ({health['duplication']['backend']['percent']}% exact, "
            f"{health['duplication']['backend']['structural_percent']}% renamed). Lines per implemented story went from "
            f"{per_story[0]} to {per_story[1]} over {len(health['growth'])} commits: each feature costs a little more code than the last."),

    cards("Engineering plan", "Refactor the hot core while the product is still young", "", [
        ("Split the repository", "Per-domain stores (process, agent, twin, context, datasets) and services; orchestration out of the persistence layer."),
        ("A walk engine", "The orchestrator's graph walk as a small state machine with parallel gateways, timers and resumable steps, replacing nested closures."),
        ("Remove hidden cycles", "Jobs register through entry points, not by importing the app; package boundaries checked in CI."),
        ("CI with gates", "Tests, type checks (mypy/pyright), lint, code-health ratchet, docs check and dependency audit on every change."),
        ("Contract tests", "Pydantic schemas versioned for the JSON columns, with migrations that rewrite old payloads."),
        ("Frontend structure", "A query cache instead of per-page polling; split the large panels; end-to-end tests of the main journeys."),
    ]),

    table("Security and compliance", "The assurance a regulated buyer will need", "", ["Item", "What it takes", "When"], [
        ["SOC 2 Type II", "Controls, evidence automation, 3-6 month observation window", "Start in phase 1, report around month 12"],
        ["ISO 27001", "ISMS, risk register, internal audit", "Alongside SOC 2"],
        ["GDPR", "DPIA template, records of processing, data subject request tooling, EU hosting option", "Phase 1"],
        ["EU AI Act", "Logging, human-oversight evidence, transparency for high-risk use; the twin's evidence export is a head start", "Phase 2"],
        ["DORA / bank outsourcing", "Exit plans, sub-processor register, resilience testing", "Phase 3, banking customers"],
        ["Penetration test", "External test before the first production pilot, then yearly", "End of phase 1"],
    ]),

    roadmap("Roadmap", "Twelve months to enterprise grade", "Phases overlap. Each ends in something a customer can use.", [
        ("Phase 0 · Safe to pilot", "Weeks 0-6", [
            "OIDC SSO and real roles", "Secrets manager, object storage, encryption at rest",
            "CI with tests and the code-health and docs gates", "Worker split from the API; append-only run steps"]),
        ("Phase 1 · Multi-tenant platform", "Months 2-5", [
            "tenant_id and row-level security", "Kubernetes/Helm, autoscaled API and workers",
            "OpenTelemetry, alerts, audit log", "LLM gateway with failover and budgets", "Pen test; SOC 2 controls start"]),
        ("Phase 2 · Agents in production", "Months 4-8", [
            "Agent runtime reusing the twin's gateway", "Agent identity and scoped credentials",
            "Human-review inbox; kill switch; canary", "Live monitoring and drift against the twin"]),
        ("Phase 3 · Certified and at scale", "Months 6-12", [
            "SOC 2 Type II, ISO 27001", "DR drills; in-region deployments; customer-managed keys",
            "Refactor complete: repository split, walk engine", "Parallel gateways and capacity simulation in the twin"]),
    ]),

    table("Team and effort", "Indicative team (estimate)", "A planning figure to size the investment, not a quote.",
          ["Role", "Phase 0", "Phases 1-2", "Phase 3"], [
              ["Platform / SRE", "1", "2-3", "2"],
              ["Backend", "2", "3-4", "3"],
              ["Frontend", "1", "1-2", "1"],
              ["Security and compliance", "0.5", "1", "1 + external auditor"],
              ["Evaluation / QA", "0.5", "1", "1"],
              ["Product and design", "1", "1-2", "1"],
              ["Total", "~6", "~9-13", "~9"],
          ], note="Roughly 110-130 person-months over 12 months, plus cloud, audit and pen-test costs. Reuse of the existing twin design shortens the agent runtime considerably."),

    table("Risks", "What could derail it, and the mitigation", "", ["Risk", "Mitigation"], [
        ["Tenancy retrofit touches everything", "Do it first, behind RLS, before more features; schema linting in CI"],
        ["Generated agents are not accurate enough for production", "Release gate on statistical evidence, stronger models per operation, human checkpoints by default"],
        ["Customers can't supply as-is data for the business case", "Templates and default benchmarks per process type; time & motion capture in the operating model"],
        ["Model costs at volume", "Prompt caching, smaller models where evaluation allows, per-tenant budgets"],
        ["Building our own runtime competes with customers' agent platforms", "Offer both: our runtime, or a certified hand-off via MCP / A2A with twin evidence attached"],
    ]),

    statement("Decisions needed", "Four decisions to make now",
              "These shape the plan more than any engineering choice:", [
                  "Deployment model: multi-tenant SaaS, dedicated single-tenant, or both from day one?",
                  "Runtime strategy: run agents ourselves, hand them to customer platforms, or both?",
                  "Model strategy: which providers and regions to support for regulated customers (EU-hosted, private)?",
                  "First certified market: banking (DORA, outsourcing rules) or broader enterprise (SOC 2 first)?",
              ]),

    cards("Next 30 days", "Quick wins that de-risk the first pilot", "", [
        ("SSO behind a flag", "OIDC login with the customer's IdP; remove self-chosen roles."),
        ("Turn on the gates", "CI running the 600 tests, the code-health ratchet and the docs check."),
        ("Split the worker", "A separate worker deployment from the same image; the API stops running jobs."),
        ("Append-only steps", "Move run steps to their own table; the biggest scale fix for the least code."),
        ("Secrets and storage", "Secrets out of .env; uploads to object storage with encryption."),
        ("Pilot evidence pack", "Process twin report, compliance evidence export and a DPIA draft for the first customer."),
    ]),
]

if __name__ == "__main__":
    build(slides, Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None,
          title="Axyntro -- Enterprise readiness")
