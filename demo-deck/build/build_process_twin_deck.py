"""Builds the process twin demo deck: Meridian's "Invoice to Pay" run end
to end as a digital twin, with every agent, before production.

Every number comes from the seeded run's result files in
demo-deck/process-twin/artifacts/results (written by
backend/scripts/demo_seed/seed_process_twin.py), and every picture from
demo-deck/process-twin/screenshots (capture.mjs). Rebuild after a new run:

    python build_process_twin_deck.py ../axyntro-process-twin-invoice-to-pay.html ../axyntro-process-twin-invoice-to-pay.pptx
"""

import json
import sys
from collections import Counter
from pathlib import Path

from deck_kit import bars, build, cards, compare, flow, kpis, section, shot, statement, table, title

ROOT = Path(__file__).resolve().parents[1] / "process-twin"
ART, RES, SHOTS = ROOT / "artifacts", ROOT / "artifacts" / "results", ROOT / "screenshots"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


v1 = load(RES / "process-twin-report-as generated.json")
rpt = load(RES / "process-twin-report-repeat.json")
v2 = load(RES / "process-twin-report-after fixes.json")
agents = load(RES / "agents.json")
mapping = load(RES / "system-mapping.json")
fixes = load(RES / "fixes.json")
model = load(ART / "02-operating-model.json")
cases = load(ART / "04-cases.json")
systems = {p.stem: load(p) for p in sorted((ART / "03-system-data").glob("*.json"))}
rehearsal = load(RES / "rehearsal-run.json")


def pct(x):
    return "--" if x is None else f"{round(x * 100)}%"


def rng(i, f=pct):
    return f"{f(i['low'])}-{f(i['high'])}"


def hrs(minutes):
    return "--" if minutes is None else (f"{minutes / 60:.1f} h" if minutes >= 120 else f"{minutes:.0f} min")


def eur(x):
    return "--" if x is None else f"EUR {x:,.2f}"


def eur0(x):
    return "--" if x is None else f"EUR {x / 1e6:.1f}m" if abs(x) >= 1e6 else f"EUR {x:,.0f}"


def num(x, d=1):
    return "--" if x is None else f"{x:,.{d}f}"


LABELS = {f"Task_{i}": label for i, t, label, *_ in [
    ("t1", "", "Capture invoice data"), ("t2", "", "Verify supplier"), ("t3", "", "Reject and notify supplier"),
    ("t4", "", "Check duplicates and fraud signals"), ("t5", "", "Investigate suspected fraud"),
    ("t6", "", "Match to PO and goods receipt"), ("t7", "", "Resolve match exception"),
    ("t8", "", "Code to GL and check VAT"), ("t9", "", "Approve spend"), ("t10", "", "Post to ledger"),
    ("t11", "", "Schedule payment"), ("t12", "", "Send remittance advice")]}
ROLES = {n["node_id"]: n["role"] for n in model["nodes"]}
agent_nodes = {nid for a in agents for nid in a["node_ids"] if nid.startswith("Task_")}

today_fte = v2["as_is_touch_minutes"]["mean"] / 60 * model["monthly_volume"] * 12 / model["productive_hours_per_fte"]
kinds = Counter(c["kind"] for c in cases)
expected = {c["kind"]: c["labels"]["decision"] for c in cases}
route_text = {c["kind"]: c["labels"]["outputs"]["expected_route"] for c in cases}
total_records = sum(len(s["records"]) for s in systems.values())


def seg_rows(report):
    return {s["segment"]: s for s in report["segments"]}


s1, s2 = seg_rows(v1), seg_rows(v2)

# Consistency: the same invoices, the same seed, run twice.
by_row_1 = {c["row_key"]: c for c in v1["cases_detail"]}
by_row_r = {c["row_key"]: c for c in rpt["cases_detail"]}
flips = [(k, by_row_1[k], by_row_r[k]) for k in sorted(by_row_1) if k in by_row_r
         and (by_row_1[k]["outcome"] or by_row_1[k]["status"]) != (by_row_r[k]["outcome"] or by_row_r[k]["status"])]
right_wrong = [k for k, a, b in flips if a["correct"] != b["correct"]]
agent_fixes = [f for f in fixes if "agent" in f]


def what_happened(c) -> str:
    """'Node 'Task_t8' (Code invoice ...): reason' -> 'Code invoice ...: reason'."""
    if c["failure"]:
        import re as _re
        text = _re.sub(r"Node '(Task_\w+)' \([^)]*\): ", lambda m: LABELS.get(m.group(1), m.group(1)) + ": ", c["failure"])
        return text.replace("Final response was not a valid JSON object", "final answer not valid JSON")[:110]
    steps = [LABELS.get(p, p) for p in c["path"] if p.startswith("Task")]
    return f"routed to {c['outcome']} after: {steps[-1] if steps else '?'}"
sandbox_fixes = [f for f in fixes if "system" in f]


def shot_or_none(name, *args, **kw):
    path = SHOTS / f"{name}.png"
    return [shot(*args[:2], path, *args[2:], **kw)] if path.exists() else []


slides = [
    title("Axyntro · Process digital twin", "Invoice to Pay, rehearsed end to end before it is real",
          f"Meridian Group's supplier-invoice process run as a digital twin: {len({a['id'] for a in agents})} AI agents, "
          f"the people who stay in the loop, eight simulated systems and {len(cases)} realistic invoices, measured "
          "against how the process runs today.",
          "Arrow keys or click to move · F for full screen"),

    cards("The question", "A process owner doesn't ask 'does the agent work?'", "They ask three things the twin answers with evidence:", [
        ("Does it work together?", "Nine agents that each pass their own tests can still fail as a process: a hand-off loses a field, or a gateway routes on nothing."),
        ("Is it actually better?", "'86% automatable' is a forecast. Cycle time, people's time and cost per case, against today, are evidence."),
        ("Where does it break?", "Which kind of invoice goes wrong, which step still needs people, and what to fix before go-live."),
    ]),

    flow("The process today", "Invoice to Pay at Meridian Group Finance",
         f"{model['monthly_volume']:,} supplier invoices a month. Every step is done by a person today, roughly "
         f"{today_fte:.0f} FTE of hands-on work, before counting the days invoices wait in queues.", [
             ("Invoice received", "", "event"),
             *[(LABELS[n], ROLES.get(n, ""), "person") for n in
               ("Task_t1", "Task_t2", "Task_t4", "Task_t6", "Task_t7", "Task_t8", "Task_t9", "Task_t10", "Task_t11", "Task_t12")],
             ("Invoice paid", "", "event")],
         note="Plus three exits: rejected (unknown, blocked or sanctioned supplier), blocked (duplicate or fraud signal), and exceptions resolved with buyer and supplier."),

    flow("The to-be process", "Agents for the work, people for judgement and authority",
         f"Axyntro's blueprint, grounded in policy AP-02, proposed an agent for {len(agent_nodes)} of the 12 steps. Fraud "
         "investigation and spend approval stay with people"
         + ("." if len(agent_nodes) >= 10 else "; this blueprint also left " + ", ".join(
             LABELS[n].lower() for n in LABELS if n not in agent_nodes and n not in ("Task_t5", "Task_t9")) + " with a person."), [
             ("Invoice received", "", "event"),
             *[(LABELS[n], "agent" if n in agent_nodes else ROLES.get(n, "person"), "agent" if n in agent_nodes else "person")
               for n in ("Task_t1", "Task_t2", "Task_t4", "Task_t5", "Task_t6", "Task_t7", "Task_t8", "Task_t9", "Task_t10", "Task_t11", "Task_t12")],
             ("Invoice paid", "", "event")]),

    *shot_or_none("03-agents", "The agents", "One generated agent per step", [
        f"{len({a['id'] for a in agents})} agents, each with purpose, inputs and outputs, tools, permissions, guardrails and escalation",
        "Human checkpoints where the policy asks for review",
        "Every agent simulation-ready: typed outputs, tool contracts, a test scenario",
    ], "The agents are specifications Axyntro can test, not prompts in a notebook."),

    cards("The twin's ingredients", "Four things make a process twin", "", [
        ("Agents", f"{len({a['id'] for a in agents})} generated agents, one per automatable step."),
        ("A world", f"8 simulated systems answering from {total_records} sample records: vendor master, purchase orders, ledger, sanctions list, tax rules, inbox, payments, email."),
        ("Cases", f"{len(cases)} invoices across {len(kinds)} kinds, each with the outcome policy AP-02 requires."),
        ("Today's baseline", f"An operating model from a time and motion study: handling and waiting times per step, the cost of four roles, {model['monthly_volume']:,} invoices a month."),
    ]),

    table("The simulated world", "Systems answer from sample data, not invention",
          "Each agent's tools are wired to records, so a lookup returns the same answer every time and the right outcome is knowable.",
          ["System", "Records", "What it holds", "Agent systems it serves"],
          [[name, str(len(d["records"])), d["description"][:70],
            ", ".join(sorted(k for k, v in mapping["mapping"].items() if name in v))[:90] or "--"]
           for name, d in ((d["system"], d) for d in systems.values())],
          note=f"Not backed by data (an LLM plays them): {', '.join(mapping['unmatched']) or 'none'}."),

    table("Ground truth", "Invoices with planted problems and known right answers",
          "Generated from facts, not labelled by a model: a duplicate is in the ledger, a fraud signal is a fresh bank account that doesn't match.",
          ["Kind of invoice", "Cases", "Right outcome under AP-02"],
          [[k.replace("_", " "), str(n), f"{expected[k]} -- {route_text[k]}"] for k, n in kinds.most_common()]),

    *shot_or_none("04-operating-model", "Today's baseline", "How the process runs today, step by step", [
        "Hands-on time and queue time per step, as minimum / most likely / maximum",
        "Hourly cost per role: AP team, controller, budget holder, treasury",
        "Volume: 9,000 invoices a month; 1,600 productive hours per FTE",
    ], "Savings are measured against these numbers, not against zero."),

    flow("One case, end to end", f"The rehearsal: {rehearsal.get('row_key') or 'one clean invoice'} through every agent",
         "A case file carries every fact forward. Agents decide judgement gateways; the confirmed AP-02 rule decides the EUR 10,000 approval.",
         [(n.replace("Task_", "").replace("Gateway_", "Gate ").replace("Event_", ""), LABELS.get(n, ""),
           "agent" if n in agent_nodes else "gate" if n.startswith("Gateway") else "event" if n.startswith("Event") else "person")
          for n in (rehearsal.get("visited_path") or [])][:18],
         note=f"Status {rehearsal.get('status')}; AI cost ${rehearsal.get('total_cost_usd') or 0:.4f} for the whole case."
              + (" A clean invoice, yet the matching agent sent it to exception handling (t7): the kind of behaviour the "
                 "twin then measures over many cases." if "Task_t7" in (rehearsal.get("visited_path") or []) else "")),

    section("Run 1", "The twin, as generated", f"{v1['cases']} invoices, every agent, every decision point."),

    kpis("Run 1 · as generated", "What the first run showed", v1["headline"], [
        ("Finished end to end", f"{v1['completed']}/{v1['cases']}", f"{v1['failed']} cases fell back to a person"),
        ("Straight through", pct(v1["straight_through"]["rate"]), f"no person involved ({rng(v1['straight_through'])})"),
        ("Right outcome", pct(v1["accuracy"]["rate"]), f"against AP-02 ({rng(v1['accuracy'])}, 95% confidence)"),
        ("AI cost per invoice", f"${v1['llm_cost_per_case_usd']['mean']:.3f}", "every agent turn included"),
    ]),

    table("Run 1 · where it breaks", "Results by kind of invoice", "Weakest first. A strong average can hide a weak segment.",
          ["Kind of invoice", "Cases", "Right outcome", "Straight through"],
          [[s["segment"].replace("_", " "), str(s["cases"]), f"{pct(s['accuracy']['rate'])} ({s['accuracy']['passed']}/{s['accuracy']['total']})",
            pct(s["straight_through"])] for s in v1["segments"]],
          tone=["bad" if (s["accuracy"]["rate"] or 0) < 0.6 else "warn" if (s["accuracy"]["rate"] or 0) < 0.9 else "good" for s in v1["segments"]]),

    table("What the twin found", "Defects found before production, not after",
          "Each of these would have reached production unseen in a step-by-step agent test. From run 1's case table:",
          ["Invoice", "Kind", "Expected", "Twin", "What happened"],
          [[c["row_key"], c["segment"].replace("_", " "), c["expected_outcome"] or "", c["outcome"] or c["status"].lower(),
            what_happened(c)]
           for c in v1["cases_detail"] if c["correct"] is False][:10],
          tone=["bad" if c["expected_outcome"] == "escalate" and c["outcome"] == "approve" else "warn"
                for c in v1["cases_detail"] if c["correct"] is False][:10],
          note="Critical: a fraud signal that ends in payment. Warnings: wrong outcome, or a case that fell back to a person."),

    table("Run 2 · the same invoices again", "Same invoices, same seed, different answers",
          f"Repeating run 1 exactly changed the outcome of {len(flips)} of {len(by_row_1)} invoices; for {len(right_wrong)} "
          "it changed whether the answer was right. Agents built on language models aren't deterministic, and only repeated runs show it.",
          ["Invoice", "Kind", "Expected", "Run 1", "Repeat"],
          [[k, a["segment"].replace("_", " "), a["expected_outcome"] or "", a["outcome"] or a["status"].lower(),
            b["outcome"] or b["status"].lower()] for k, a, b in flips[:10]],
          tone=["bad" if (a["correct"] != b["correct"]) else "warn" for k, a, b in flips[:10]],
          tone_labels={"bad": "Right <-> wrong", "warn": "Other change"},
          note=f"Right outcome: run 1 {pct(v1['accuracy']['rate'])}, repeat {pct(rpt['accuracy']['rate'])}. A release gate "
               "should require repeated runs per case (Epic 34 trials) before trusting a single number."),

    section("Run 3", "Fix what the twin found, then run again",
            "Sandbox and runtime fixes, the same 30 invoices, the same seed."),

    statement("The fixes", "What changed between run 1 and run 3", "", [
        *[f"Sandbox: '{f['system']}' is now backed by {', '.join(f['after'])} (was {', '.join(f['before']) if f['before'] else 'an LLM'})."
          for f in sandbox_fixes],
        *[f"Agent '{f['agent']}' given {', '.join(f['added'])} (a governance edit, US20.8)." for f in agent_fixes],
        "Runtime: an agent whose final answer isn't valid JSON gets one recorded repair turn, as a production runtime would do.",
    ]),

    table("Run 1 vs run 3", "Same invoices, same seed: the effect of the fixes", "",
          ["Measure", "Run 1 (as generated)", "Run 3 (after fixes)"], [
              ["Finished end to end", f"{v1['completed']}/{v1['cases']}", f"{v2['completed']}/{v2['cases']}"],
              ["Right outcome", f"{pct(v1['accuracy']['rate'])} ({rng(v1['accuracy'])})", f"{pct(v2['accuracy']['rate'])} ({rng(v2['accuracy'])})"],
              ["Straight through", pct(v1["straight_through"]["rate"]), pct(v2["straight_through"]["rate"])],
              ["People time per invoice", hrs(v1["to_be_touch_minutes"]["mean"]), hrs(v2["to_be_touch_minutes"]["mean"])],
              ["AI cost per invoice", f"${v1['llm_cost_per_case_usd']['mean']:.3f}", f"${v2['llm_cost_per_case_usd']['mean']:.3f}"],
              *[[f"  {k.replace('_', ' ')}", f"{pct(s1[k]['accuracy']['rate'])}", f"{pct(s2[k]['accuracy']['rate'])}"]
                for k in sorted(s1, key=lambda k: s1[k]["accuracy"]["rate"] or 0)[:5] if k in s2],
          ], note="Both runs use the same 30 invoices and seed. Run-to-run variation (previous slide) is of the same order as some of "
                  "these differences: read them with their intervals."),

    bars("Efficiency", "Today vs with agents, per invoice (run 3)",
         "The same invoices costed both ways, on the same random draws, so the difference is the process.", [
             ("Cycle time (working time, waits + handling)", v2["as_is_cycle_minutes"]["mean"], v2["to_be_cycle_minutes"]["mean"],
              hrs(v2["as_is_cycle_minutes"]["mean"]), hrs(v2["to_be_cycle_minutes"]["mean"])),
             ("People's hands-on time", v2["as_is_touch_minutes"]["mean"], v2["to_be_touch_minutes"]["mean"],
              hrs(v2["as_is_touch_minutes"]["mean"]), hrs(v2["to_be_touch_minutes"]["mean"])),
             ("Cost per invoice (labour + AI)", v2["as_is_cost_per_case"]["mean"], v2["to_be_cost_per_case"]["mean"],
              eur(v2["as_is_cost_per_case"]["mean"]), eur(v2["to_be_cost_per_case"]["mean"])),
         ], note=f"Cycle time {pct(v2['cycle_time_reduction']['mean'])} shorter ({rng(v2['cycle_time_reduction'])}); "
                 f"people's time {pct(v2['touch_time_reduction']['mean'])} lower ({rng(v2['touch_time_reduction'])}). 95% paired bootstrap ranges."),

    kpis("The business case", f"At {model['monthly_volume']:,} invoices a month", "Extrapolated from the twin's sample; every assumption is stated in the report.", [
        ("People hours released", f"{num(v2['annual_hours_released']['mean'], 0)}", f"a year ({rng(v2['annual_hours_released'], lambda v: num(v, 0))})"),
        ("FTE capacity", num(v2["annual_fte_released"]["mean"]), f"of about {today_fte:.0f} today ({rng(v2['annual_fte_released'], num)})"),
        ("Cost saved", eur0(v2["annual_cost_saved"]["mean"]), f"a year ({rng(v2['annual_cost_saved'], eur0)})"),
        ("Cycle time", pct(v2["cycle_time_reduction"]["mean"]), "shorter, waits included"),
    ], note=" ".join(v2["assumptions"][2:4])),

    table("Step by step", "Where people stay, and what each agent costs", "", [
        "Step", "Done by", "Cases", "Failed", "Agent time", "Asks for review", "People time today", "With agents"], [
        [n["label"][:44], "Agent" if n["kind"] == "agent" else "Person", str(n["visits"]), str(n["failures"]),
         f"{n['mean_agent_seconds']:.0f} s" if n["mean_agent_seconds"] else "--", pct(n["review_rate"]),
         f"{n['as_is_touch_minutes']:.0f} min", f"{n['to_be_touch_minutes']:.1f} min"] for n in v2["nodes"]],
        tone=["warn" if n["failures"] else "" for n in v2["nodes"]]),

    *shot_or_none("06-report-headline", "In the product", "The process twin report", [
        "Headline in plain language, with confidence ranges",
        "Today vs with agents, on one scale per measure",
        "Per step, per kind of case, outcome matrix, routes taken, every case drillable",
    ], "The same view a process owner uses to decide go or no-go."),
    *shot_or_none("08-segments", "In the product", "By kind of case, and the outcome matrix", [
        "Weak segments highlighted, never colour alone",
        "Expected vs actual outcome for every labelled invoice",
    ]),

    compare("Before production", "What the twin proves, and what it doesn't yet",
            "Proven in the twin", [
                "The whole process works together, on realistic data",
                "Which invoices go wrong, and why, per step and per kind",
                "The business case, on stated assumptions, with ranges",
                "Fixes measured on the same cases before they ship",
            ],
            "Still needed for production", [
                "A production agent runtime with identity and a kill switch",
                "Real connectors to ERP, vendor master and bank",
                "Queueing under load (waits are sampled, not simulated)",
                "Enterprise identity, tenancy and data protection (see the enterprise-readiness deck)",
            ]),

    cards("Reproduce it", "Everything in this deck is one command away", "", [
        ("Generate the data", "python demo-deck/process-twin/generate_data.py: deterministic suppliers, POs, ledger, invoices and labels."),
        ("Seed and run", ".\\start.ps1 -Demo -ResetDemo -SeedProcessTwin: policy, process, blueprint, agents, world, both twin runs."),
        ("Open it", "Process 'Invoice to Pay' -> Process twin: operating model, runs, report."),
        ("Rebuild the deck", "capture.mjs for screenshots, then build_process_twin_deck.py."),
    ]),
]

if __name__ == "__main__":
    build(slides, Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None,
          title="Axyntro -- Invoice to Pay process twin")
