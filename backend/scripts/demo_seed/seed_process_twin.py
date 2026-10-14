"""Seeds the "Invoice to Pay" process twin demo (Epic 36) through the real API.

The story: Meridian Group's Group Finance wants to automate supplier
invoice processing. This builds the whole process as a digital twin and
runs it over realistic invoices before anything reaches production:

  1  Organization context: Group Finance, approval limits, the AP policy AP-02 (Epics 27-28)
  2  The as-is process (12 steps, 4 decision points, 3 endings), finalized
  3  Blueprint and one generated agent per automatable step (Epics 7, 12)
  4  Simulated systems backed by sample data: inbox, vendor master, sanctions,
     ERP purchasing, AP ledger, tax rules, payments, email (US36.1)
  5  The as-is operating model from a time & motion study (US36.4)
  6  75 labelled invoices with planted exceptions; the approval-limit rule (Epics 32-33)
  7  The whole-process rehearsal: case file, agent- and rule-routed gateways (US36.2-36.3)
  8  One rehearsal run, then the process twin over the invoices (US36.5-36.7)
  9  Fix what the twin found -- give agents the systems they lack (US20.8) -- and run it again

All data comes from demo-deck/process-twin/artifacts (run generate_data.py
to regenerate it). Real LLM calls: policy extraction, blueprint, rule
drafting, and every agent turn in the runs.

    python backend/scripts/demo_seed/seed_process_twin.py --cases 40 --budget 3

Standard library only, so it runs from the host without the backend venv.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_demo import Api, ApiError, act, say, wait_for  # noqa: E402

ARTIFACTS = Path(__file__).resolve().parents[3] / "demo-deck" / "process-twin" / "artifacts"
PROCESS_NAME = "Invoice to Pay"
MANUAL_NODES = {
    "Task_t5": "Investigating suspected fraud needs a person's judgement and authority (AP-02 2.1, 2.2).",
    "Task_t9": "Approving spend above EUR 10,000 is the Budget Holder's authority (AP-02 5.1).",
}
ACTIVE = ("CREATED", "QUEUED", "RUNNING", "WAITING_FOR_MODEL", "WAITING_FOR_TOOL")


def load(name: str) -> Any:
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


class ProcessTwinSeeder:
    def __init__(self, api: Api, args: argparse.Namespace):
        self.api = api
        self.args = args
        self.timeout_s = args.timeout_minutes * 60
        self.ids: dict[str, str] = {}
        self.agents: dict[str, dict[str, Any]] = {}  # node id -> artifact

    def pid(self, path: str = "") -> str:
        return f"/api/processes/{self.ids['process']}{path}"

    def save(self, name: str, data: Any) -> None:
        out = ARTIFACTS / "results"
        out.mkdir(exist_ok=True)
        (out / name).write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    # -- 0 --

    def preflight(self) -> None:
        act("Preflight")
        try:
            self.api.get("/healthz", timeout=10)
        except Exception as exc:  # noqa: BLE001 -- any failure means the stack isn't up
            raise SystemExit(f"The API at {self.api.base_url} isn't reachable ({exc}). Start the stack first.")
        self.api.post("/api/auth/login", {"name": "Demo Presenter", "role": "editor"})
        if any(p["name"] == PROCESS_NAME for p in self.api.get("/api/processes")):
            raise SystemExit(f"An '{PROCESS_NAME}' process already exists -- seed an empty demo database "
                             "(.\\start.ps1 -Demo -ResetDemo) or delete it first")
        if not (ARTIFACTS / "04-cases.json").exists():
            raise SystemExit(f"No sample data in {ARTIFACTS} -- run demo-deck/process-twin/generate_data.py first")
        say(f"logged in to {self.api.base_url} as 'Demo Presenter' (editor)")

    # -- 1 --

    def organization(self) -> None:
        act("1 -- Organization context: Group Finance and policy AP-02")
        self.api.post("/api/organization/profile", {
            "name": "Meridian Group", "industry": "banking", "size": "mid-size",
            "countries": ["EU", "DE", "NL", "FR", "ES", "IT", "IE"],
            "business_units": ["Group Finance"],
            "approval_rules": [
                {"role": "Budget Holder", "decision_type": "invoice spend", "limit_amount": 10000, "currency": "EUR"},
                {"role": "Finance Controller", "decision_type": "invoice spend", "limit_amount": 100000, "currency": "EUR"},
            ],
            "apply_packs": [],
        })
        units = self.api.get("/api/organization/entities", params={"kind": "business_unit", "status": "all"})
        finance = next(u for u in units if u["name"] == "Group Finance")
        self.ids["finance"] = finance["id"]
        say("Meridian Group profile with Group Finance; Budget Holder up to EUR 10,000, Finance Controller up to EUR 100,000")

        policy = ARTIFACTS / "00-ap-policy-AP-02.txt"
        document = self.api.request(
            "POST", "/api/organization/documents",
            files={"file": (policy.name, policy.read_bytes(), "text/plain")},
            form={"title": "Accounts Payable Policy", "code": "AP-02", "business_unit_id": finance["id"],
                  "jurisdictions": "EU", "effective_date": "2026-01-01"},
        )
        say("uploaded AP-02; extracting rules (LLM)...")

        def extracted() -> dict[str, Any] | None:
            doc = self.api.get(f"/api/organization/documents/{document['id']}")
            if doc["status"] == "failed":
                raise SystemExit(f"Policy extraction failed: {doc.get('error_message')}")
            return doc if doc["status"] == "done" else None

        doc = wait_for(extracted, what="policy extraction", timeout_s=self.timeout_s)
        confirmed = self.api.post("/api/organization/entities/bulk-confirm", {"min_confidence": 0.0})
        say(f"AP-02: {doc['proposal_count']} rules extracted, {len(confirmed['confirmed'])} confirmed")

    # -- 2 --

    def process(self) -> None:
        act("2 -- The as-is process")
        process = self.api.post("/api/processes", {
            "name": PROCESS_NAME,
            "scope": {"business_unit_id": self.ids["finance"], "jurisdictions": ["EU"],
                      "process_type": "accounts_payable", "data_classes": []},
        })
        self.ids["process"] = process["id"]
        self.api.put(self.pid("/bpmn"), {"xml": (ARTIFACTS / "01-invoice-to-pay.bpmn").read_text(encoding="utf-8")})
        self.api.post(self.pid("/gap-findings/analyze"))
        for finding in self.api.get(self.pid("/gap-findings"), params={"status": "open"}):
            self.api.post(self.pid(f"/gap-findings/{finding['id']}/dismiss"))
        self.api.post(self.pid("/finalize"))
        say(f"process {process['id']}: 12 steps, 4 decision points, 3 endings; finalized")

    # -- 3 --

    def blueprint_and_agents(self) -> None:
        act("3 -- Blueprint and agents")
        overlay = None
        for attempt in (1, 2, 3):
            say(f"generating the blueprint (LLM, attempt {attempt})...")
            try:
                overlay = self.api.post(self.pid("/blueprint/generate"), {})
            except ApiError as exc:
                if exc.status != 502 or attempt == 3:
                    raise
                say(f"the model's reply was unusable ({exc.detail}); trying again")
                continue
            with_spec = [n for n in overlay["nodes"] if n.get("agent_spec") and n["node_id"] not in MANUAL_NODES
                         and n["node_id"].startswith("Task_")]
            if len(with_spec) >= 8:
                break
        for node_id, why in MANUAL_NODES.items():
            node = next((n for n in overlay["nodes"] if n["node_id"] == node_id), None)
            if node and node["verdict"] != "not_automatable":
                overlay = self.api.patch(self.pid(f"/blueprint/nodes/{node_id}"),
                                         {"verdict": "not_automatable", "justification": why})
        # Agents for the work steps; the walk itself routes gateways and ends.
        automatable = [n for n in overlay["nodes"] if n.get("agent_spec") and n["verdict"] != "not_automatable"
                       and n["node_id"].startswith("Task_")]
        say(f"blueprint: {len(overlay['nodes'])} steps; {len(automatable)} with an agent; "
            f"fraud investigation and spend approval stay with people")

        for node in automatable:
            if node["node_id"] in self.agents:
                continue  # already covered by a consolidated group's artifact
            artifact = self.api.post(self.pid(f"/blueprint/nodes/{node['node_id']}/agent-artifact"))
            for member in artifact["node_ids"]:
                self.agents[member] = artifact
        distinct = {a["id"]: a for a in self.agents.values()}
        self.ids["agent_count"] = str(len(distinct))
        for artifact in distinct.values():
            d = artifact["definition"]
            say(f"agent '{d['name']}' for {artifact['node_ids']}: systems {d['tools_systems_needed']}, "
                f"checkpoint '{d['human_checkpoint']}'")
        self.save("agents.json", list(distinct.values()))

    # -- 4 --

    def _world(self) -> tuple[dict[str, Any], dict[str, list[str]], list[str]]:
        """Sample data per system: each dataset under its own name (for agents
        given it later), and every system name an agent uses, backed by all
        the datasets its name points at -- "ERP Core (AP/MM)" gets both the
        purchase orders and the ledger."""
        datasets = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ARTIFACTS / "03-system-data").glob("*.json"))]
        names = sorted({s for a in self.agents.values() for s in a["definition"]["tools_systems_needed"]})

        def entry(parts: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "description": "; ".join(d["description"] for d in parts),
                "records": [r for d in parts for r in d["records"]],
                "lookup_fields": list(dict.fromkeys(f for d in parts for f in d["lookup_fields"])),
                "operations": list(dict.fromkeys(o for d in parts for o in d["operations"])),
                "reference_prefix": parts[0]["reference_prefix"],
            }

        system_data = {d["system"]: entry([d]) for d in datasets}
        mapping: dict[str, list[str]] = {}
        unmatched = []
        for name in names:
            lowered = f" {name.lower()} "
            parts = [d for d in datasets if any(k in lowered for k in d["keywords"])]
            if parts:
                system_data[name] = entry(parts)
                mapping[name] = [d["system"] for d in parts]
            else:
                unmatched.append(name)
        return system_data, mapping, unmatched

    def environment(self) -> None:
        act("4 -- Simulated systems backed by sample data")
        system_data, mapping, unmatched = self._world()
        env = self.api.post(self.pid("/twin-environments"), {
            "name": "Meridian AP sandbox (sample data, Oct 2026)",
            "systems": sorted(system_data),
            "system_data": system_data,
        })
        self.ids["environment"] = env["id"]
        for name, parts in mapping.items():
            say(f"{name} -> {', '.join(parts)}")
        if unmatched:
            say(f"not backed by sample data (the LLM simulates them): {unmatched}")
        self.save("system-mapping.json", {"mapping": mapping, "unmatched": unmatched})

    # -- 5 --

    def operating_model(self) -> None:
        act("5 -- How the process runs today")
        model = load("02-operating-model.json")
        saved = self.api.put(self.pid("/process-twin/operating-model"), model)
        say(f"operating model v{saved['version']}: {model['monthly_volume']:,} invoices a month, "
            f"{len(model['nodes'])} steps timed, {len(model['roles'])} roles costed")

    # -- 6 --

    def dataset_and_rules(self) -> None:
        act("6 -- Labelled invoices and the approval-limit rule")
        cases = load("04-cases.json")
        ds = self.api.post(self.pid("/datasets"), {
            "name": "Supplier invoices -- October sample",
            "description": "75 invoices with known right outcomes, including planted duplicates, fraud signals, "
                           "price variances, missing goods receipts and supplier problems.",
        })
        self.ids["dataset"] = ds["id"]
        self.api.post(self.pid(f"/datasets/{ds['id']}/rows"), [
            {"row_key": c["row_key"], "inputs": c["inputs"], "labels": c["labels"], "segment": c["segment"],
             "critical": c["critical"], "provenance": "synthetic", "tags": [c["kind"]]}
            for c in cases
        ])
        say(f"dataset: {len(cases)} invoices across {len({c['segment'] for c in cases})} kinds")

        settings = load("05-scenario.json")
        say("drafting decision rules from the diagram (LLM)...")
        rules = self.api.post(self.pid("/decision-rules/propose"))
        wanted = settings["rules"]["Gateway_g4"]
        confirmed = 0
        for rule in rules:
            if rule["gateway_id"] == "Gateway_g4" and rule["branch_target_id"] in wanted:
                self.api.patch(self.pid(f"/decision-rules/{rule['id']}"), {
                    "expression": wanted[rule["branch_target_id"]], "confirm": True,
                    "outcome": "approve",
                })
                confirmed += 1
        if confirmed != 2:
            raise SystemExit(f"Expected two drafted rules for 'Invoice above EUR 10,000?', confirmed {confirmed}")
        say("confirmed: amount_eur > 10,000 -> Budget Holder approval; otherwise straight to posting")

    # -- 7 --

    def scenario(self) -> None:
        act("7 -- The whole-process rehearsal")
        settings = load("05-scenario.json")
        first_clean = next(c for c in load("04-cases.json") if c["kind"] == "clean_small")
        reviewer = settings["reviewer"]
        scenario = self.api.post(self.pid("/orchestration/scenarios"), {
            "name": settings["name"],
            "inputs": first_clean["inputs"],
            "gateway_decisions": {},
            "manual_node_config": settings["manual_node_config"],
            "system_stubs": {},
            "human_checkpoint_config": {node: reviewer for node in self.agents},
            "data_mapping_mode": {},
            "expected_path": [],
            "expected_final_output": {},
            "environment_id": self.ids["environment"],
            "handoff": settings["handoff"],
            "default_gateway_routing": settings["default_gateway_routing"],
            "gateway_routing": settings["gateway_routing"],
        })
        self.ids["scenario"] = scenario["id"]
        say("case file hand-off; 'Supplier verified?', 'Duplicate or fraud suspected?' and 'Matched within tolerance?' "
            "routed by the agent before them; 'Invoice above EUR 10,000?' by the confirmed rule")

        # Each agent needs a test scenario to be simulation-ready (US23.3):
        # one unit test per agent, on the same clean invoice.
        for artifact in {a["id"]: a for a in self.agents.values()}.values():
            self.api.post(self.pid(f"/agent-artifacts/{artifact['id']}/scenarios"), {
                "name": "Unit check: clean invoice", "inputs": first_clean["inputs"], "expected_steps": [],
                "expected_outputs": {}, "step_match": "in_order", "human_checkpoint_config": reviewer,
                "environment_id": self.ids["environment"],
            })

        self.rehearse()

    # -- 8 --

    def process_twin(self, label: str) -> dict[str, Any]:
        act(f"8 -- The process twin over the invoices ({label})")
        request = {"scenario_id": self.ids["scenario"], "dataset_id": self.ids["dataset"],
                   "name": f"October invoices -- {label}", "budget_usd": self.args.budget, "seed": 7,
                   "sample_size": self.args.cases or None}
        est = self.api.post(self.pid("/process-twin/estimate"), request)
        say(f"{est['cases']} invoices, estimated ${est['total_cost_usd']:.2f} ({est['basis']})")
        batch = self.api.post(self.pid("/process-twin/batches"), request)
        self.ids[f"batch_{label}"] = batch["id"]

        def finished() -> dict[str, Any] | None:
            current = self.api.get(self.pid(f"/process-twin/batches/{batch['id']}"))
            return None if current["status"] in ("running", "paused") else current

        done = wait_for(finished, what=f"{est['cases']} invoices through every agent", timeout_s=self.timeout_s * 3, every_s=20)
        report = self.api.get(self.pid(f"/process-twin/batches/{batch['id']}/report"))
        self.save(f"process-twin-report-{label}.json", report)
        say(f"{done['status']}: {done['finished']}/{done['cases']} cases, spent ${done['spent_usd']:.2f}")
        say(report["headline"])
        for seg in report["segments"]:
            say(f"  {seg['segment']}: {seg['accuracy']['passed']}/{seg['accuracy']['total']} right")
        return report

    # -- 9 --

    # What each step needs to do its job under AP-02, by the sample-data
    # system that holds it.
    NEEDS = {
        "Task_t2": ["Vendor master", "Sanctions screening"],
        "Task_t4": ["AP ledger", "Vendor master"],
        "Task_t6": ["ERP purchasing"],
    }

    def fix_agents(self) -> None:
        """US20.8: give agents the systems the first run showed they lack,
        without regenerating them."""
        act("9 -- Fix what the twin found")
        mapping = json.loads((ARTIFACTS / "results" / "system-mapping.json").read_text(encoding="utf-8"))["mapping"]
        changes = []
        for node_id, needed in self.NEEDS.items():
            artifact = self.agents.get(node_id)
            if artifact is None:
                continue
            current = self.api.get(self.pid("/blueprint/agent-artifacts"))
            artifact = next(a for a in current if a["id"] == artifact["id"])
            systems = artifact["definition"]["tools_systems_needed"]
            reachable = {d for s in systems for d in mapping.get(s, [])}
            missing = [n for n in needed if n not in reachable]
            if not missing:
                continue
            self.api.patch(self.pid(f"/blueprint/agent-artifacts/{artifact['id']}/governance"),
                           {"tools_systems_needed": systems + missing})
            changes.append({"agent": artifact["definition"]["name"], "step": node_id, "added": missing})
            say(f"'{artifact['definition']['name']}' ({node_id}) could not reach {missing}: added")
        # The sandbox itself: a system an agent writes to (document
        # generation, notifications) must accept writes. Run 1 showed the
        # remittance agent looping on a read-only "document" system.
        saved = json.loads((ARTIFACTS / "results" / "system-mapping.json").read_text(encoding="utf-8"))
        system_data, mapping, unmatched = self._world()
        moved = {name: (saved["mapping"].get(name), parts) for name, parts in mapping.items() if saved["mapping"].get(name) != parts}
        if moved:
            env = self.api.get(self.pid("/twin-environments"))[0]
            self.api.put(self.pid(f"/twin-environments/{env['id']}"), {
                "name": env["name"], "systems": sorted(system_data), "system_data": system_data,
            })
            for name, (before, after) in moved.items():
                say(f"sandbox: '{name}' now backed by {after} (was {before or 'an LLM'})")
                changes.append({"system": name, "before": before, "after": after})
            self.save("system-mapping.json", {"mapping": mapping, "unmatched": unmatched})
        if not changes:
            say("nothing to fix: every agent reaches the systems its step needs")
        self.save("fixes.json", changes)

    def resume(self) -> None:
        """--resume: pick up an already-seeded process at the rehearsal (after
        fixing something in the code), reading back what steps 1-7 made."""
        act("Resume")
        self.api.post("/api/auth/login", {"name": "Demo Presenter", "role": "editor"})
        process = next((p for p in self.api.get("/api/processes") if p["name"] == PROCESS_NAME), None)
        if process is None:
            raise SystemExit(f"No '{PROCESS_NAME}' process to resume -- run without --resume first")
        self.ids["process"] = process["id"]
        for artifact in self.api.get(self.pid("/blueprint/agent-artifacts")):
            for node in artifact["node_ids"]:
                if node.startswith("Task_"):
                    self.agents[node] = artifact
        self.ids["environment"] = self.api.get(self.pid("/twin-environments"))[0]["id"]
        self.ids["dataset"] = self.api.get(self.pid("/datasets"))[0]["id"]
        self.ids["scenario"] = self.api.get(self.pid("/orchestration/scenarios"))[0]["id"]
        self.save("agents.json", list({a["id"]: a for a in self.agents.values()}.values()))
        say(f"process {process['id']}: {len({a['id'] for a in self.agents.values()})} agents, scenario {self.ids['scenario']}")

    def rehearse(self) -> None:
        settings = load("05-scenario.json")
        first_clean = next(c for c in load("04-cases.json") if c["kind"] == "clean_small")
        say(f"rehearsal run on {first_clean['row_key']} (one clean invoice, every agent)...")
        run = self.api.post(self.pid(f"/orchestration/scenarios/{self.ids['scenario']}/run"))

        def finished() -> dict[str, Any] | None:
            current = self.api.get(self.pid(f"/orchestration/runs/{run['id']}"))
            return None if current["status"] in ACTIVE else current

        done = wait_for(finished, what="the rehearsal run", timeout_s=self.timeout_s, every_s=5)
        self.ids["rehearsal_run"] = done["id"]
        say(f"rehearsal {done['status']}: path {' -> '.join(done['visited_path'])}; cost ${done['total_cost_usd'] or 0:.4f}")
        if done["deviations"]:
            say(f"deviations: {[d['reason'] for d in done['deviations']]}")
        self.save("rehearsal-run.json", {**done, "row_key": first_clean["row_key"], "settings": settings["name"]})

    def run(self) -> None:
        if self.args.resume:
            self.resume()
            if self.args.fix_and_rerun:
                self.fix_agents()
                self.process_twin("after fixes")
                self.save("ids.json", self.ids)
                return
            act("7 -- The whole-process rehearsal (resumed)")
            self.rehearse()
            self.twin_runs()
            return
        self.preflight()
        self.organization()
        self.process()
        self.blueprint_and_agents()
        self.environment()
        self.operating_model()
        self.dataset_and_rules()
        self.scenario()
        self.twin_runs()

    def twin_runs(self) -> None:
        if not self.args.skip_batch:
            self.process_twin("as generated")
            # The same invoices and seed again: how consistent are the agents?
            self.process_twin("repeat")
            self.fix_agents()
            self.process_twin("after fixes")
        self.save("ids.json", self.ids)
        act("Done")
        say(f"process {self.ids['process']}: open it, then 'Process twin ->'")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--cases", type=int, default=0, help="invoices to run (stratified sample; 0 = all 75)")
    parser.add_argument("--budget", type=float, default=5.0, help="USD cap for the process twin run (default 5)")
    parser.add_argument("--skip-batch", action="store_true", help="set everything up but don't run the process twin")
    parser.add_argument("--resume", action="store_true", help="continue an already-seeded process from the rehearsal")
    parser.add_argument("--fix-and-rerun", action="store_true", help="with --resume: apply step 9's fixes and run the twin again")
    parser.add_argument("--timeout-minutes", type=float, default=45)
    args = parser.parse_args()
    try:
        ProcessTwinSeeder(Api(args.base_url), args).run()
    except ApiError as exc:
        raise SystemExit(f"\nAPI error: {exc}") from None


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
