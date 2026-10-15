"""Seeds a demo environment for Epics 27-35 through the real API.

Builds one end-to-end story on the "Customer Onboarding" process of a
fictional bank, Meridian Group:

  Act 1  organization profile, business units, approval limits, starter
         packs, and the ONB-01 policy extracted into confirmed rules (Epic 27)
  Act 2  a context-aware blueprint and agent that cite those rules (Epic 28)
  Act 3  an application catalog with OpenAPI specs, sandbox endpoints, and
         classified data assets, linked to the process's systems (Epic 29)
  Act 4  one compliance situation per rule the agent follows, run once so
         the evidence view is populated (Epic 30)
  Act 5  a labelled dataset: expert edge cases, a metamorphic variant set,
         a pseudonymized historical import, confirmed decision rules,
         oracle labels, label-first generated cases, and a holdout (32-33)
  Act 6  a correctness batch on agent v1, a small load batch, then v2 and a
         second batch -- plus a (disabled) nightly schedule (Epic 34)
  Act 7  release criteria, the statistical gate, and a v1/v2 comparison (35)
  Extra  business-unit model policy, a restricted member, and two disabled
         connectors for the federation story (Epic 31)

Uses real LLM calls (policy extraction, blueprint, rule drafting,
label-first generation, and every twin run). With the defaults that is a
few hundred calls on the cheap Qwen route -- roughly $1-2 and 20-40
minutes, mostly the two batches. `--skip-batches` stops after Act 5.

Run it against an empty demo database (see start.ps1 -Demo). It refuses to
run when a "Customer Onboarding" process already exists.

    python backend/scripts/demo_seed/seed_demo.py
    python backend/scripts/demo_seed/seed_demo.py --base-url http://localhost:8000 --generated-rows 40

Standard library only, so it runs from the host without the backend venv.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ASSETS = Path(__file__).resolve().parent / "assets"
PROCESS_NAME = "Customer Onboarding"
AGENT_NODE_ID = "Task_t5"  # "Assess and set initial credit limit" in customer-onboarding.bpmn

# Decision rules for the diagram's three gateways, keyed by (gateway label,
# branch condition). Proposed by the model first (US33.1), then set to these
# and confirmed, as a person reviewing the drafts would.
CANONICAL_RULES: dict[tuple[str, str], tuple[dict[str, Any], str]] = {
    ("Identity verified?", "Yes - identity verified"): (
        {"field": "identity_check", "op": "eq", "value": "passed"}, "approve"),
    ("Identity verified?", "No - verification failed"): (
        {"field": "identity_check", "op": "ne", "value": "passed"}, "reject"),
    ("Potential sanctions match?", "Yes - potential match"): (
        {"field": "sanctions_screening", "op": "eq", "value": "potential match"}, "escalate"),
    ("Potential sanctions match?", "No - clear"): (
        {"field": "sanctions_screening", "op": "ne", "value": "potential match"}, "approve"),
    ("Requested limit above EUR 50,000?", "Above EUR 50,000"): (
        {"field": "requested_limit_eur", "op": "gt", "value": 50000}, "escalate"),
    ("Requested limit above EUR 50,000?", "EUR 50,000 or less"): (
        {"field": "requested_limit_eur", "op": "lte", "value": 50000}, "approve"),
}

# A system name the blueprint might use -> the catalog application it means,
# for names the catalog's own exact/alias matching doesn't resolve.
SYSTEM_KEYWORDS = {
    "sanction": "Sanctions Screening Service",
    "identity": "Identity Verification Service",
    "kyc": "Identity Verification Service",
    "credit": "Credit Decisioning Engine",
    "scor": "Credit Decisioning Engine",
    "decision": "Credit Decisioning Engine",
    "core": "Core Banking System",
    "bank": "Core Banking System",
    "account": "Core Banking System",
    "notif": "Customer Notifications",
    "email": "Customer Notifications",
}


def _case(key: str, name: str, identity: str, sanctions: str, limit: int, income: int, country: str = "DE") -> dict[str, Any]:
    first, last = name.split(" ", 1)
    return {
        "application_id": f"APP-DEMO-{key.upper()}",
        "customer_name": name,
        "email": f"{first.lower()}.{last.lower().replace(' ', '')}@example.{country.lower()}",
        "country": country,
        "identity_check": identity,
        "sanctions_screening": sanctions,
        "requested_limit_eur": limit,
        "annual_income_eur": income,
    }


# Expert-labelled edge cases (US32.1). Critical rows must all pass the gate.
EXPERT_ROWS: list[dict[str, Any]] = [
    {"row_key": "edge-limit-exactly-50k", "critical": True, "segment": "edge-cases", "decision": "approve",
     "inputs": _case("e1", "Hannah Becker", "passed", "clear", 50000, 140000)},
    {"row_key": "edge-limit-just-over-50k", "critical": True, "segment": "edge-cases", "decision": "escalate",
     "inputs": _case("e2", "Marco Bianchi", "passed", "clear", 50001, 150000, "IT")},
    {"row_key": "edge-sanctions-small-limit", "critical": True, "segment": "edge-cases", "decision": "escalate",
     "inputs": _case("e3", "Viktor Petrov", "passed", "potential match", 10000, 60000)},
    {"row_key": "edge-identity-failed", "critical": True, "segment": "edge-cases", "decision": "reject",
     "inputs": _case("e4", "Julia Schmidt", "failed", "clear", 10000, 52000)},
    {"row_key": "edge-everything-wrong", "critical": True, "segment": "edge-cases", "decision": "reject",
     "inputs": _case("e5", "Omar Haddad", "failed", "potential match", 90000, 80000, "FR")},
    {"row_key": "typical-small", "critical": False, "segment": "typical", "decision": "approve",
     "inputs": _case("t1", "Lena Vogel", "passed", "clear", 15000, 61000)},
    {"row_key": "typical-medium", "critical": False, "segment": "typical", "decision": "approve",
     "inputs": _case("t2", "Pierre Laurent", "passed", "clear", 35000, 98000, "FR")},
    {"row_key": "typical-large", "critical": False, "segment": "typical", "decision": "escalate",
     "inputs": _case("t3", "Sanne de Vries", "passed", "clear", 75000, 210000, "NL")},
    {"row_key": "typical-very-large", "critical": False, "segment": "typical", "decision": "escalate",
     "inputs": _case("t4", "Carlos Ruiz", "passed", "clear", 120000, 260000, "ES")},
    {"row_key": "edge-low-income-high-request", "critical": False, "segment": "edge-cases", "decision": "approve",
     "inputs": _case("e6", "Aoife Byrne", "passed", "clear", 45000, 24000, "IE")},
]
AMBIGUOUS_ROW = (
    "edge-low-income-high-request",
    "The documented rule only looks at the amount. A EUR 45,000 limit on EUR 24,000 income passes it, "
    "but an analyst would likely decline on affordability.",
    "reject",
)

HISTORICAL_MAPPING = [
    {"column": "Application ID", "role": "input", "field": "application_id", "pseudonymize": "hash"},
    {"column": "Customer Name", "role": "input", "field": "customer_name", "pseudonymize": "fake"},
    {"column": "Email", "role": "input", "field": "email", "pseudonymize": "fake"},
    {"column": "IBAN", "role": "input", "field": "iban", "pseudonymize": "fake"},
    {"column": "Country", "role": "input", "field": "country", "pseudonymize": "keep"},
    {"column": "Identity Check", "role": "input", "field": "identity_check", "pseudonymize": "keep"},
    {"column": "Sanctions Screening", "role": "input", "field": "sanctions_screening", "pseudonymize": "keep"},
    {"column": "Requested Limit (EUR)", "role": "input", "field": "requested_limit_eur", "pseudonymize": "keep"},
    {"column": "Annual Income (EUR)", "role": "input", "field": "annual_income_eur", "pseudonymize": "keep"},
    {"column": "Channel", "role": "segment", "field": None, "pseudonymize": "keep"},
    {"column": "Final Status", "role": "decision", "field": None, "pseudonymize": "keep"},
]

RUN_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "ESCALATED"}
BATCH_ACTIVE = {"running", "paused"}


# ---- HTTP -----------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, method: str, path: str, status: int, detail: Any):
        super().__init__(f"{method} {path} -> {status}: {detail}")
        self.status = status
        self.detail = detail


class JobFailed(Exception):
    pass


class Api:
    """A logged-in session, cookies and all, like the browser's.

    Epic 37, US37.12: like the frontend, it sends the session's CSRF token
    on writes, and on a 401 swaps its refresh cookie for new ones and
    retries. The seed can wait on long jobs for longer than a session's
    idle timeout, so if the refresh is refused too it signs in again."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._csrf: str | None = None
        self._login_body: Any = None

    def request(self, method: str, path: str, **kw: Any) -> Any:
        try:
            result = self._send(method, path, **kw)
        except ApiError as exc:
            if exc.status != 401 or path.startswith("/api/auth/") or not self._renew():
                raise
            result = self._send(method, path, **kw)
        if path == "/api/auth/login":
            self._login_body = kw.get("json_body")
        if path in ("/api/auth/login", "/api/auth/me") and isinstance(result, dict):
            self._csrf = result.get("csrf_token", self._csrf)
        return result

    def _renew(self) -> bool:
        for path, body in (("/api/auth/refresh", {}), ("/api/auth/login", self._login_body)):
            if body is None:
                continue
            try:
                self._csrf = self._send("POST", path, json_body=body)["csrf_token"]
                return True
            except ApiError:
                continue
        return False

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        form: dict[str, str] | None = None,
        timeout: float = 300,
    ) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"Accept": "application/json"}
        if method not in ("GET", "HEAD", "OPTIONS") and self._csrf:
            headers["X-CSRF-Token"] = self._csrf
        data = None
        if files is not None or form is not None:
            data, content_type = _multipart(files or {}, form or {})
            headers["Content-Type"] = content_type
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self._opener.open(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                detail = json.loads(raw).get("detail", raw)
            except (json.JSONDecodeError, AttributeError):
                detail = raw
            raise ApiError(method, path, exc.code, detail) from None
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body.decode(errors="replace")

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("POST", path, json_body=body if body is not None else {}, **kw)

    def put(self, path: str, body: Any, **kw: Any) -> Any:
        return self.request("PUT", path, json_body=body, **kw)

    def patch(self, path: str, body: Any, **kw: Any) -> Any:
        return self.request("PATCH", path, json_body=body, **kw)


def _multipart(files: dict[str, tuple[str, bytes, str]], form: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in form.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for name, (filename, content, content_type) in files.items():
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _file(name: str, content_type: str) -> tuple[str, bytes, str]:
    return name, (ASSETS / name).read_bytes(), content_type


# ---- output ---------------------------------------------------------------


def act(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def say(message: str) -> None:
    print(f"  - {message}", flush=True)


def wait_for(check, *, what: str, timeout_s: float, every_s: float = 5.0) -> Any:
    """Polls `check()` until it returns something truthy."""
    deadline = time.monotonic() + timeout_s
    last_note = 0.0
    while True:
        result = check()
        if result:
            return result
        if time.monotonic() > deadline:
            raise SystemExit(f"Timed out after {timeout_s / 60:.0f} min waiting for {what}")
        if time.monotonic() - last_note > 60:
            say(f"waiting for {what}...")
            last_note = time.monotonic()
        time.sleep(every_s)


# ---- seeding --------------------------------------------------------------


# Epic 37, US37.15: the demo is performed by people in distinct roles, so
# segregation of duties is visible: nobody approves what they changed.
# Arjun is an architect *and* an approver -- he can approve others' agents,
# not his own (the scripted "approve button shows why" moment).
PERSONAS: dict[str, tuple[str, list[str], str | None]] = {
    "admin": ("Sam Okafor (Admin)", ["admin"], None),
    "owner": ("Olivia Brandt (Process Owner)", ["process_owner"], None),
    "auditor": ("Morgan Reyes (Auditor)", ["auditor"], None),
    "architect": ("Arjun Mehta (Automation Architect)", ["architect", "analyst", "approver"], None),
    "analyst": ("Ana Costa (Process Analyst)", ["analyst"], "retail"),
    "approver": ("Avery Lindqvist (Approver)", ["approver"], "retail"),
}


class Seeder:
    def __init__(self, api: Api, args: argparse.Namespace):
        self.base_url = api.base_url
        self.api = api
        self.args = args
        self.timeout_s = args.timeout_minutes * 60
        self.ids: dict[str, str] = {}
        self.personas: dict[str, Api] = {}

    def as_(self, key: str) -> Api:
        """Signs in (once) as the persona and makes it the acting user."""
        if key not in self.personas:
            name, roles, unit = PERSONAS[key]
            persona = Api(self.base_url)
            body: dict[str, Any] = {"name": name, "roles": roles}
            if unit is not None:
                body["business_unit_id"] = self.ids[unit]
            persona.post("/api/auth/login", body)
            self.personas[key] = persona
        self.api = self.personas[key]
        return self.api

    # -- helpers --

    def entities(self, kind: str, status: str = "confirmed") -> list[dict[str, Any]]:
        return self.api.get("/api/organization/entities", params={"kind": kind, "status": status})

    def entity_named(self, kind: str, name: str) -> dict[str, Any]:
        for entity in self.entities(kind, status="all"):
            if entity["name"].lower() == name.lower():
                return entity
        raise SystemExit(f"Expected a {kind} named '{name}' but found none")

    def pid(self, path: str = "") -> str:
        return f"/api/processes/{self.ids['process']}{path}"

    # -- preflight --

    def preflight(self) -> None:
        act("Preflight")
        try:
            self.api.get("/healthz", timeout=10)
        except (urllib.error.URLError, ApiError, OSError) as exc:
            raise SystemExit(f"The API at {self.api.base_url} isn't reachable ({exc}). Start the stack first.")
        self.as_("admin")
        self.as_("auditor")
        self.as_("owner")
        say(f"signed in to {self.base_url}; the demo cast: " + ", ".join(p[0] for p in PERSONAS.values()))
        existing = [p for p in self.api.get("/api/processes") if p["name"] == PROCESS_NAME]
        if existing:
            raise SystemExit(
                f"A '{PROCESS_NAME}' process already exists ({existing[0]['id']}). Seed an empty demo database "
                "instead -- e.g. .\\start.ps1 -Demo -ResetDemo -Seed"
            )

    # -- Act 1: organization context (Epic 27) --

    def organization(self) -> None:
        act("Act 1 -- Organization context (Epic 27)")
        self.as_("owner")
        result = self.api.post(
            "/api/organization/profile",
            {
                "name": "Meridian Group",
                "industry": "banking",
                "size": "mid-size",
                "countries": ["EU", "DE", "FR", "NL", "ES", "IT", "IE"],
                "business_units": ["Retail Banking", "Wealth Management"],
                "applications": [],
                "approval_rules": [
                    {"role": "Head of Operations", "decision_type": "credit limit", "limit_amount": 50000, "currency": "EUR"},
                    {"role": "Chief Risk Officer", "decision_type": "credit limit", "limit_amount": 500000, "currency": "EUR"},
                ],
                "apply_packs": ["eu-uk-aml-basics", "eu-data-protection-basics"],
            },
        )
        say(f"profile applied: created {result['created']}, packs {result['applied_packs']}")

        retail = self.entity_named("business_unit", "Retail Banking")
        wealth = self.entity_named("business_unit", "Wealth Management")
        self.ids["retail"], self.ids["wealth"] = retail["id"], wealth["id"]
        self.api.put(
            f"/api/organization/entities/{retail['id']}",
            {"data": {"description": "Current accounts, cards and personal lending for EU retail customers."},
             "change_note": "demo seed"},
        )
        # Epic 31, US31.8: Wealth Management keeps model calls in the EU with
        # PII redacted. The provider's region is unset by default, so a
        # Wealth process would be refused -- the policy working as designed.
        self.api.put(
            f"/api/organization/entities/{wealth['id']}",
            {"data": {"description": "Private banking and investment advice.",
                      "llm_policy": {"residency": ["EU"], "redact_pii": True}},
             "change_note": "demo seed: EU-only model policy"},
        )
        say("business units: Retail Banking, Wealth Management (EU-only model policy, PII redaction)")

        for name, definition, synonyms in [
            ("MLRO", "Money Laundering Reporting Officer: decides on potential sanctions matches and suspicious activity.",
             ["Money Laundering Reporting Officer"]),
            ("Account activation", "Enabling the customer to transact on the account.", ["activate account"]),
        ]:
            self.api.post("/api/organization/entities",
                          {"kind": "glossary_term", "name": name, "data": {"definition": definition, "synonyms": synonyms}})
        say("glossary: MLRO, Account activation")

        document = self.api.request(
            "POST", "/api/organization/documents",
            files={"file": _file("onboarding-policy.txt", "text/plain")},
            form={"title": "Customer Onboarding Policy", "code": "ONB-01", "business_unit_id": retail["id"],
                  "jurisdictions": "EU", "effective_date": "2026-01-01"},
        )
        say("uploaded policy ONB-01; extracting rules (LLM)...")

        def extracted() -> dict[str, Any] | None:
            doc = self.api.get(f"/api/organization/documents/{document['id']}")
            if doc["status"] == "failed":
                raise SystemExit(f"Policy extraction failed: {doc.get('error_message')}")
            return doc if doc["status"] == "done" else None

        doc = wait_for(extracted, what="policy extraction", timeout_s=self.timeout_s)
        confirmed = self.api.post("/api/organization/entities/bulk-confirm", {"min_confidence": 0.0})
        say(f"policy extracted: {doc['proposal_count']} proposals; confirmed {len(confirmed['confirmed'])}"
            + (f", left {len(confirmed['skipped'])} for review" if confirmed["skipped"] else ""))

    # -- Act 3 (first half): catalog, before the blueprint so systems link (Epic 29) --

    def catalog(self) -> None:
        act("Act 3 -- Application & data catalog (Epic 29)")
        self.as_("architect")
        csv_file = {"file": _file("applications.csv", "text/csv")}
        preview = self.api.request("POST", "/api/organization/applications/import", files=csv_file, params={"dry_run": "true"})
        self.api.request("POST", "/api/organization/applications/import", files=csv_file, params={"dry_run": "false"})
        say(f"imported {len(preview['rows'])} applications from applications.csv")

        assets = {}
        for name, classification, description in [
            ("Customer", "Confidential-PII", "Identity, contact details and KYC results."),
            ("Account", "Confidential", "Accounts, balances and credit limits."),
            ("Credit decision", "Confidential", "Scores, recommended limits and decision reasons."),
            ("Sanctions screening result", "Restricted", "Screening hits and MLRO decisions (kept five years)."),
        ]:
            entity = self.api.post("/api/organization/entities", {
                "kind": "data_asset", "name": name,
                "data": {"classification": classification, "residency": ["EU"], "description": description},
            })
            assets[name] = entity["id"]
        say("data assets: Customer (Confidential-PII), Account, Credit decision, Sanctions screening result (Restricted)")

        holdings = {
            "Core Banking System": ["Customer", "Account"],
            "Credit Decisioning Engine": ["Customer", "Credit decision"],
            "Sanctions Screening Service": ["Customer", "Sanctions screening result"],
            "Identity Verification Service": ["Customer"],
            "Customer Notifications": ["Customer"],
        }
        for app_name, asset_names in holdings.items():
            app = self.entity_named("application", app_name)
            self.ids[f"app:{app_name}"] = app["id"]
            data = {**app["data"], "data_assets": [assets[a] for a in asset_names]}
            self.api.put(f"/api/organization/entities/{app['id']}", {"data": data, "change_note": "demo seed: data assets"})

        for app_name, spec, host in [
            ("Credit Decisioning Engine", "credit-decisioning-openapi.json", "https://credit-engine.sandbox.meridian.example"),
            ("Core Banking System", "core-banking-openapi.json", "https://core-banking.sandbox.meridian.example"),
        ]:
            app_id = self.ids[f"app:{app_name}"]
            result = self.api.request("PUT", f"/api/organization/applications/{app_id}/api-spec",
                                      files={"file": _file(spec, "application/json")})
            self.api.put(f"/api/organization/applications/{app_id}/endpoints", [
                {"url": host, "environment": "sandbox", "description": "Vendor sandbox"},
                {"url": host.replace(".sandbox", ""), "environment": "production",
                 "description": "Recorded for reference only -- never called from a twin"},
            ])
            say(f"{app_name}: OpenAPI '{result['title']}' ({len(result['operations'])} operations), sandbox + production endpoints")

    # -- process --

    def process(self) -> None:
        act("Process -- Customer Onboarding (curated diagram)")
        self.as_("analyst")
        process = self.api.post("/api/processes", {
            "name": PROCESS_NAME,
            "scope": {"business_unit_id": self.ids["retail"], "jurisdictions": ["DE"],
                      "process_type": "customer_onboarding", "data_classes": []},
        })
        self.ids["process"] = process["id"]
        xml = (ASSETS / "customer-onboarding.bpmn").read_text(encoding="utf-8")
        self.api.put(self.pid("/bpmn"), {"xml": xml})
        self.api.post(self.pid("/gap-findings/analyze"))
        for finding in self.api.get(self.pid("/gap-findings"), params={"status": "open"}):
            self.api.post(self.pid(f"/gap-findings/{finding['id']}/dismiss"))
        version = self.api.post(self.pid("/finalize"))
        say(f"process {process['id']} scoped to Retail Banking / DE (EU rules apply via the hierarchy); "
            "Ana finalized it")
        # Epic 37, US37.4: the baseline counts once someone who didn't edit it approves.
        self.as_("owner").post(self.pid(f"/versions/{version['id']}/approve"), {"comment": "Reviewed with operations"})
        say("Olivia (process owner) approved the baseline")

    # -- Act 2: blueprint and agent (Epic 28) --

    def blueprint_and_agent(self) -> None:
        act("Act 2 -- Context-aware blueprint and agent (Epic 28)")
        self.as_("architect")
        node = None
        for attempt in (1, 2):
            say(f"generating blueprint (LLM, attempt {attempt})...")
            overlay = self.api.post(self.pid("/blueprint/generate"), {})
            node = next((n for n in overlay["nodes"] if n["node_id"] == AGENT_NODE_ID), None)
            if node and node.get("agent_spec"):
                break
        if not node or not node.get("agent_spec"):
            raise SystemExit("The blueprint gave 'Assess and set initial credit limit' no agent spec twice -- re-run the seed")
        cited = sum(len(n.get("citations") or []) for n in overlay["nodes"])
        say(f"blueprint: {len(overlay['nodes'])} steps, {cited} rule citations; credit-limit step is "
            f"'{node['verdict']}' with checkpoint '{node['agent_spec'].get('human_checkpoint')}'")

        self.link_systems()
        artifact = self.api.post(self.pid(f"/blueprint/nodes/{AGENT_NODE_ID}/agent-artifact"))
        self.ids["agent"] = artifact["id"]
        definition = artifact["definition"]
        say(f"agent '{definition['name']}' v{artifact['version']}: {len(definition.get('knowledge_sources') or [])} "
            f"knowledge sources, {len(definition.get('tool_contracts') or [])} tool contracts")

    def link_systems(self) -> None:
        resolutions = self.api.get(self.pid("/systems"))
        linked = []
        for res in resolutions:
            if res["status"] in ("linked", "matched"):
                linked.append(f"{res['system_name']} (auto)")
                continue
            lowered = res["system_name"].lower()
            target = next((app for word, app in SYSTEM_KEYWORDS.items() if word in lowered), None)
            if target is None:
                continue
            self.api.put(self.pid(f"/systems/{urllib.parse.quote(res['system_name'], safe='')}"),
                         {"application_id": self.ids[f"app:{target}"]})
            linked.append(f"{res['system_name']} -> {target}")
        say("systems linked: " + ("; ".join(linked) if linked else "none needed"))

    # -- Act 5: labelled dataset and oracles (Epics 32-33) --

    def dataset(self) -> None:
        act("Act 5 -- Labelled dataset and oracles (Epics 32-33)")
        self.as_("architect")
        dataset = self.api.post(self.pid("/datasets"), {
            "name": "Credit limit decisions -- golden set",
            "description": "Expert edge cases, pseudonymized 2025 history, and rule-labelled generated cases.",
            "agent_artifact_id": self.ids["agent"],
        })
        self.ids["dataset"] = dataset["id"]
        ds = f"/datasets/{dataset['id']}"

        self.api.post(self.pid(f"{ds}/rows"), [
            {"row_key": r["row_key"], "inputs": r["inputs"], "labels": {"decision": r["decision"]},
             "segment": r["segment"], "provenance": "expert", "critical": r["critical"]}
            for r in EXPERT_ROWS
        ])
        key, note, second = AMBIGUOUS_ROW
        self.api.put(self.pid(f"{ds}/rows/{key}/ambiguity"),
                     {"ambiguous": True, "note": note, "second_labels": {"decision": second}})
        say(f"{len(EXPERT_ROWS)} expert cases (5 critical); one marked ambiguous")

        relation = self.api.post(self.pid(f"{ds}/relations"), {
            "name": "Name casing doesn't change the decision", "kind": "invariance",
            "field": "customer_name", "perturbation": {"type": "uppercase"},
        })
        variants = self.api.post(self.pid(f"{ds}/relations/{relation['id']}/variants"))
        say(f"metamorphic relation '{relation['name']}': {variants['variants']} variants")

        csv_file = {"file": _file("historical-credit-decisions.csv", "text/csv")}
        preview = self.api.request("POST", self.pid(f"{ds}/import/preview"), files=csv_file)
        imported = self.api.request("POST", self.pid(f"{ds}/import"), files=csv_file,
                                    form={"mapping": json.dumps(HISTORICAL_MAPPING)})
        say(f"historical import: {imported['added']} of {preview['total_rows']} rows "
            "(IDs hashed; names, emails and IBANs replaced with look-alikes)")

        self.rules()
        labelled = self.api.post(self.pid(f"{ds}/oracle/label"))
        conflicts = self.api.get(self.pid(f"{ds}/oracle/conflicts"))
        say(f"oracle: {labelled['agreed']} labels agree with the rules, {len(conflicts)} conflicts to review, "
            f"{labelled['labelled']} filled in")

        if self.args.generated_rows:
            self.generate(ds)

        summary = self.api.post(self.pid(f"{ds}/holdout"), {"fraction": 0.1, "seed": 7})
        say(f"dataset v{summary['version']}: {summary['row_count']} rows, {summary['holdout_count']} held out; "
            f"segments {summary['segments']}")

    def generate(self, ds: str) -> None:
        """Label-first generation (US33.4). One job is one long LLM loop, so
        a failure is retried once and then skipped -- the dataset is still
        usable without generated cases."""
        before = self.api.get(self.pid(ds))["row_count"]
        for attempt in (1, 2):
            started = self.api.post(self.pid(f"{ds}/generate"),
                                    {"total": self.args.generated_rows, "messy_fraction": 0.1, "segment": "generated"})
            if not started["queued"]:
                break
            say(f"label-first generation of {self.args.generated_rows} cases queued (LLM, attempt {attempt})...")
            try:
                self.wait_job(started["job_id"], "label-first generation")
                break
            except JobFailed as exc:
                say(f"label-first generation failed: {exc}")
        after = self.api.get(self.pid(ds))["row_count"]
        say(f"label-first generation kept {after - before} cases the rules agree with")

    def rules(self) -> None:
        drafted = None
        for attempt in (1, 2):
            try:
                drafted = self.api.post(self.pid("/decision-rules/propose"))
            except ApiError as exc:
                if attempt == 2:
                    raise
                say(f"rule drafting failed ({exc.detail}); retrying")
                continue
            if len(drafted) >= len(CANONICAL_RULES):
                break
        rules = self.api.get(self.pid("/decision-rules"))
        structured = sum(1 for r in rules if r.get("expression"))
        confirmed = 0
        for rule in rules:
            key = (rule["gateway_label"], rule.get("condition_text") or rule["branch_label"])
            if key not in CANONICAL_RULES:
                key = (rule["gateway_label"], rule["branch_label"])
            if key not in CANONICAL_RULES:
                say(f"leaving unrecognised rule for review: {rule['gateway_label']} / {rule['branch_label']}")
                continue
            expression, outcome = CANONICAL_RULES[key]
            self.api.patch(self.pid(f"/decision-rules/{rule['id']}"),
                           {"expression": expression, "outcome": outcome, "is_default": False, "confirm": True})
            confirmed += 1
        if confirmed < len(CANONICAL_RULES):
            raise SystemExit(f"Only {confirmed} of {len(CANONICAL_RULES)} decision rules could be confirmed")
        say(f"decision rules: model drafted {structured} of {len(rules)} as structured rules; "
            f"{confirmed} reviewed and confirmed")

    def wait_job(self, job_id: str, what: str) -> None:
        def done() -> dict[str, Any] | None:
            job = next((j for j in self.api.get("/api/jobs", params={"limit": 200}) if j["id"] == job_id), None)
            if job is None or job["status"] in ("queued", "running"):
                return None
            if job["status"] == "failed":
                raise JobFailed(job.get("last_error") or "no error recorded")
            return job
        wait_for(done, what=what, timeout_s=self.timeout_s)

    # -- Act 6: batches (Epic 34) --

    def volume_scenario(self) -> None:
        scenario = self.api.post(self.pid(f"/agent-artifacts/{self.ids['agent']}/scenarios"), {
            "name": "Credit decisions at volume",
            "dataset_id": self.ids["dataset"],
            "decision_field": "decision",
            "step_match": "in_order",
            "expected_steps": [],
            "expected_outputs": {},
            "human_checkpoint_config": {"mode": "probability", "approve_probability": 1.0},
        })
        self.ids["scenario"] = scenario["id"]
        say(f"data-driven scenario '{scenario['name']}' on the golden set")

    def batch(self, label: str, **request: Any) -> dict[str, Any]:
        scenario = self.ids["scenario"]
        body = {"tier": "full", "trials": 1, "budget_usd": self.args.budget, "seed": 7, **request}
        plan = self.api.post(self.pid(f"/scenarios/{scenario}/batches/estimate"), body)
        est = plan["estimate"]
        say(f"{label}: {est['runs']} runs, estimated ${est['total_cost_usd']:.2f} and {est['total_minutes']:.0f} min "
            f"({est['basis']})")
        batch = self.api.post(self.pid(f"/scenarios/{scenario}/batches"), body)

        def finished() -> dict[str, Any] | None:
            current = self.api.get(self.pid(f"/batches/{batch['id']}"))
            return None if current["status"] in BATCH_ACTIVE else current

        done = wait_for(finished, what=f"{label} ({est['runs']} runs)", timeout_s=self.timeout_s, every_s=10)
        progress = done["progress"]
        say(f"{label} {done['status']}: {progress['passed']} passed, {progress['failed']} failed, "
            f"{progress['errored']} errored; spent ${done['spent_usd']:.2f}")
        return done

    def batches_v1(self) -> None:
        act("Act 6 -- Batch execution at volume (Epic 34)")
        self.volume_scenario()
        if self.args.skip_batches:
            say("--skip-batches: no batches run")
            return
        self.ids["batch_v1"] = self.batch("v1 correctness batch", trials=self.args.trials)["id"]
        if not self.args.skip_load:
            self.batch("load batch", tier="smoke", mode="load", target_concurrency=8, stub_latency_ms=250)
        self.api.post(self.pid("/batch-schedules"), {
            "scenario_id": self.ids["scenario"], "tier": "regression", "trials": 1, "every_minutes": 1440,
            "on_dataset_change": True, "on_context_change": True, "budget_usd": 2.0, "enabled": False,
        })
        say("nightly regression schedule created (disabled, so it never spends on its own)")

    # -- Act 4: compliance situations (Epic 30), on the version the gate checks --

    def new_agent_version(self) -> None:
        act("Agent v2")
        self.as_("architect")
        artifact = self.api.post(self.pid(f"/blueprint/nodes/{AGENT_NODE_ID}/agent-artifact"))
        say(f"regenerated the credit-limit agent: now v{artifact['version']}")

    def compliance(self) -> None:
        act("Act 4 -- Compliance-aware testing (Epic 30)")
        agent = self.ids["agent"]
        context = self.api.get(self.pid(f"/agent-artifacts/{agent}/test-builder/context"))
        if not context["rules"]:
            say("the agent cites no testable controls -- no compliance situations to add")
            return
        runs = []
        for rule in context["rules"]:
            scenario = self.api.post(self.pid(f"/agent-artifacts/{agent}/scenarios"), _situation(rule))
            run = self.api.post(self.pid(f"/scenarios/{scenario['id']}/run"))
            runs.append((rule, run["id"]))
        say(f"{len(runs)} compliance situations created, one per rule; running them (LLM)...")

        results = []
        for rule, run_id in runs:
            def finished() -> dict[str, Any] | None:
                run = self.api.get(self.pid(f"/twin-runs/{run_id}"))
                return run if run["status"] in RUN_TERMINAL else None
            results.append((rule, wait_for(finished, what=f"situation for {rule['code'] or rule['name']}",
                                           timeout_s=self.timeout_s)))
        evidence = self.api.get(self.pid(f"/blueprint/agent-artifacts/{agent}/compliance-evidence"))
        say(f"compliance evidence: {evidence['evidenced']} of {evidence['total']} rules evidenced")
        for control in evidence["controls"]:
            say(f"    {control['status']:<11} {control['code'] or control['name']}")

    # -- Act 7: release gate (Epic 35) --

    def release(self) -> None:
        act("Act 7 -- Results and the statistical release gate (Epic 35)")
        if self.args.skip_batches:
            say("--skip-batches: no batch results to gate on")
            return
        self.ids["batch_v2"] = self.batch("v2 correctness batch", trials=self.args.trials)["id"]
        agent = self.ids["agent"]
        self.as_("approver").put(self.pid(f"/agent-artifacts/{agent}/release-criteria"), {
            "min_pass_rate_lower": 0.85, "min_segment_rate": 0.7, "critical_must_pass": True,
            "no_significant_regression": True, "tiers": ["regression", "full"],
        })
        results = self.api.get(self.pid(f"/batches/{self.ids['batch_v2']}/results"))
        say(f"v2 results: {results['reading']}")
        comparison = self.api.get(self.pid("/batch-comparison"),
                                  params={"a": self.ids["batch_v1"], "b": self.ids["batch_v2"]})
        say(f"v1 vs v2: {comparison['reading']}")
        gate = self.api.get(self.pid(f"/agent-artifacts/{agent}/release-gate"))
        say(f"release gate: {'PASSES' if gate['passed'] else 'BLOCKS'}")
        for reason in gate["reasons"]:
            say(f"    {reason}")
        self.approve_agent()

    # -- Epic 31 extras --

    def approve_agent(self) -> None:
        """Epic 37, US37.4: Arjun built the agent, so his approval is refused
        even though he holds the approver role; Avery approves it."""
        agent = self.ids["agent"]
        status = self.as_("architect").get(self.pid(f"/blueprint/agent-artifacts/{agent}/approval"))
        say(f"Arjun can't approve his own agent: {status['reason']}")
        self.as_("approver").post(self.pid(f"/blueprint/agent-artifacts/{agent}/approve"),
                                  {"comment": "Approved for pilot"})
        say("Avery (approver) approved the agent release")

    def federation(self) -> None:
        act("Federation and connectors (Epic 31)")
        self.as_("admin")
        analyst = Api(self.base_url)
        user = analyst.post("/api/auth/login", {"name": "Wealth Management Analyst", "roles": ["analyst"],
                                                "business_unit_id": self.ids["wealth"]})
        self.api.put(f"/api/organization/members/{user['id']}",
                     [{"business_unit_id": self.ids["wealth"], "steward": True}])
        say("'Wealth Management Analyst' sees and stewards only Wealth Management and group-wide context")
        for connector in [
            {"name": "Policy library (SharePoint export)", "type": "folder",
             "config": {"path": "/data/policies"}, "enabled": False, "schedule_minutes": 1440},
            {"name": "ServiceNow CMDB applications", "type": "http",
             "config": {"url": "https://meridian.service-now.example/api/now/table/cmdb_ci_appl", "token_env": "SNOW_TOKEN"},
             "enabled": False, "schedule_minutes": 360},
        ]:
            self.api.post("/api/organization/connectors", connector)
        say("connectors: policy folder and ServiceNow CMDB feed (configured, disabled -- no sync)")

    def run(self) -> None:
        started = time.monotonic()
        self.preflight()
        self.organization()
        self.catalog()
        self.process()
        self.blueprint_and_agent()
        self.dataset()
        self.batches_v1()
        self.new_agent_version()
        self.compliance()
        self.release()
        self.federation()
        act("Done")
        say(f"seeded in {(time.monotonic() - started) / 60:.0f} min")
        say(f"process: {self.ids['process']}  agent: {self.ids['agent']}  dataset: {self.ids['dataset']}")
        say("open the app and sign in as one of the cast (same name, same roles) -- e.g. Olivia to start at "
            "Organization, Arjun to see why he can't approve his own agent, Morgan to read the audit log")
        say("for a live 'policy changed' moment, upload assets/credit-limit-policy-update.txt as ONB-01: "
            "it raises the Head of Operations limit, so it arrives flagged as relaxing a rule")


def _situation(rule: dict[str, Any]) -> dict[str, Any]:
    """One business-readable situation that exercises a rule (US30.1/US30.5)."""
    text = f"{rule.get('code') or ''} {rule['name']} {rule.get('description') or ''}".lower()
    inputs = _case("c1", "Lena Vogel", "passed", "clear", 20000, 70000)
    given = "A verified customer with a clear sanctions screen asks for a EUR 20,000 limit"
    if "sanction" in text:
        inputs.update(sanctions_screening="potential match")
        given = "Screening returns a potential sanctions match on a EUR 20,000 request"
    elif "pep" in text or "politically" in text:
        inputs.update(politically_exposed_person=True)
        given = "The applicant is a politically exposed person asking for EUR 20,000"
    elif "identity" in text or "verif" in text or "cdd" in text:
        inputs.update(identity_check="failed")
        given = "Identity verification failed for a EUR 20,000 request"
    elif "limit" in text or "credit" in text or "approv" in text:
        inputs.update(requested_limit_eur=120000, annual_income_eur=260000)
        given = "A verified, clear customer asks for a EUR 120,000 limit"

    assertions = []
    if rule.get("requires_human_review"):
        assertions.append({"control_id": rule["id"], "kind": "escalated", "role": rule.get("review_role")})
    if "decision_logging" in (rule.get("required_guardrails") or []) or "log" in text or not assertions:
        assertions.append({"control_id": rule["id"], "kind": "logged"})
    label = rule.get("code") or rule["name"]
    return {
        "name": f"Rule check: {label}",
        "inputs": inputs,
        "step_match": "in_order",
        "expected_steps": [],
        "expected_outputs": {},
        "human_checkpoint_config": {"mode": "probability", "approve_probability": 1.0},
        "story": {
            "title": f"Follows {label}",
            "given": given,
            "when": "the agent assesses the credit limit",
            "then": rule.get("description") or rule["name"],
            "covers": [rule["id"]],
            "source": "manual",
            "expect": {"compliance": assertions},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--generated-rows", type=int, default=40,
                        help="label-first generated cases to request (0 to skip; default 40)")
    parser.add_argument("--trials", type=int, default=1, help="repeats per row in each correctness batch (default 1)")
    parser.add_argument("--budget", type=float, default=5.0, help="USD cap per batch (default 5)")
    parser.add_argument("--skip-batches", action="store_true", help="set everything up but run no batches")
    parser.add_argument("--skip-load", action="store_true", help="skip the small load-mode batch")
    parser.add_argument("--timeout-minutes", type=float, default=60, help="max wait for any one step (default 60)")
    args = parser.parse_args()
    try:
        Seeder(Api(args.base_url), args).run()
    except ApiError as exc:
        raise SystemExit(f"\nAPI error: {exc}") from None


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
