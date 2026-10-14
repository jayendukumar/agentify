"""Code-health CLI.

    python -m tools.code_health report     [--no-history]   # write docs/code-health/report.{md,json}
    python -m tools.code_health check                       # fail (exit 1) if health regressed vs the baseline
    python -m tools.code_health baseline                    # accept the current snapshot as the new baseline
    python -m tools.code_health mutation --module app/oracles/rules.py [--tests tests/test_oracles.py] [--max 40]

Run from the repository root. See tools/code_health/README.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import mutation
from .collect import collect
from .report import render

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "docs" / "code-health"
THRESHOLDS = Path(__file__).with_name("thresholds.json")
BASELINE = OUT / "baseline.json"


def _gate_metrics(data: dict) -> dict:
    """The handful of numbers `check` ratchets on -- small enough to diff by eye."""
    cx, dup, cyc, dead = data["complexity"], data["duplication"], data["cycles"], data["dead_code"]
    return {
        "over_cognitive_limit": cx["backend"]["over_cognitive_limit"] + cx["frontend"]["over_cognitive_limit"],
        "over_cyclomatic_limit": cx["backend"]["over_cyclomatic_limit"] + cx["frontend"]["over_cyclomatic_limit"],
        "duplication_percent_backend": dup["backend"]["percent"],
        "duplication_percent_frontend": dup["frontend"]["percent"],
        "structural_duplication_percent": round(dup["backend"]["structural_percent"] + dup["frontend"]["structural_percent"], 2),
        "module_cycles": len(cyc["backend_modules"]) + len(cyc["frontend_files"]),
        "package_cycles_incl_deferred": len(cyc["backend_packages_incl_deferred"]) + len(cyc["frontend_dirs"]),
        "unused_definitions": sum(d["status"] == "unused" for d in dead["python"] + dead["typescript"]),
        "unused_dependencies": len(data["unused_dependencies"]),
        "mutation_scores": {k: v["score"] for k, v in data["mutation"].items()},
    }


def cmd_report(args) -> int:
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    data = collect(REPO, with_history=not args.no_history)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    (OUT / "report.md").write_text(render(data, thresholds), encoding="utf-8")
    if not BASELINE.exists():
        BASELINE.write_text(json.dumps(_gate_metrics(data), indent=2), encoding="utf-8")
    print(f"Wrote {OUT / 'report.md'}")
    return 0


def cmd_baseline(args) -> int:
    data = collect(REPO, with_history=False)
    OUT.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(_gate_metrics(data), indent=2), encoding="utf-8")
    print(f"Baseline updated: {BASELINE}")
    return 0


def cmd_check(args) -> int:
    """Ratchet: nothing may get worse than the committed baseline, and the
    absolute thresholds in thresholds.json must hold."""
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    data = collect(REPO, with_history=False)
    now = _gate_metrics(data)
    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else now
    failures: list[str] = []

    for key in ("over_cognitive_limit", "over_cyclomatic_limit", "module_cycles", "package_cycles_incl_deferred",
                "unused_definitions", "unused_dependencies"):
        if now[key] > base.get(key, now[key]):
            failures.append(f"{key}: {base[key]} -> {now[key]} (must not rise)")
    for side in ("backend", "frontend"):
        key = f"duplication_percent_{side}"
        if now[key] > base.get(key, now[key]) + thresholds["duplication_ratchet_tolerance"]:
            failures.append(f"{key}: {base[key]}% -> {now[key]}%")
        if now[key] > thresholds["duplication_percent_max"]:
            failures.append(f"{key}: {now[key]}% exceeds the {thresholds['duplication_percent_max']}% ceiling")
    for module, score in now["mutation_scores"].items():
        before = base.get("mutation_scores", {}).get(module)
        if score is not None and before is not None and score < before - thresholds["mutation_ratchet_tolerance"]:
            failures.append(f"mutation score of {module}: {before:.0%} -> {score:.0%}")

    for line in failures:
        print(f"FAIL  {line}")
    if not failures:
        print("Code health: no regressions against the baseline.")
    return 1 if failures else 0


def cmd_mutation(args) -> int:
    backend = REPO / "backend"
    tests = args.tests or []
    if not tests:
        guess = backend / "tests" / f"test_{Path(args.module).stem}.py"
        if not guess.exists():
            print(f"No --tests given and {guess.name} doesn't exist; pass --tests.", file=sys.stderr)
            return 2
        tests = [guess.relative_to(backend).as_posix()]
    summary = mutation.run(backend, args.module, tests, max_mutants=args.max, seed=args.seed,
                           results_file=OUT / "mutation.json")
    print(f"Mutation score for {args.module}: {summary['score']:.0%} "
          f"({summary['killed']} killed, {summary['survived']} survived)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.code_health")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("report")
    p.add_argument("--no-history", action="store_true", help="skip git-history metrics (hotspots, growth)")
    p.set_defaults(func=cmd_report)
    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("baseline").set_defaults(func=cmd_baseline)
    p = sub.add_parser("mutation")
    p.add_argument("--module", required=True, help="path relative to backend/, e.g. app/oracles/rules.py")
    p.add_argument("--tests", nargs="*", help="test paths relative to backend/")
    p.add_argument("--max", type=int, default=40, help="max mutants to test (sampled)")
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_mutation)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
