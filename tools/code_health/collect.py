"""Runs every analyzer and assembles one JSON-serializable snapshot of the
codebase's health. Rendering (report.py) and gating (check in
__main__.py) both read this snapshot, never the analyzers directly.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from statistics import mean

from . import deadcode, duplication, graph, history
from .pyanalyze import PyFileAnalysis, analyze_tree

COGNITIVE_LIMIT = 15   # SonarSource default for "too hard to understand"
CYCLOMATIC_LIMIT = 10  # McCabe's original recommendation


def _ts_analysis(repo: Path) -> dict:
    proc = subprocess.run(
        ["node", str(Path(__file__).with_name("ts_analyze.mjs")), str(repo / "frontend"), "src"],
        capture_output=True, check=True,
    )
    files = json.loads(proc.stdout.decode("utf-8"))["files"]
    for data in files.values():
        for imp in data["imports"]:
            if imp.get("internal"):
                imp["internal"] = f"frontend/{imp['internal']}"
    return {f"frontend/{name}": data for name, data in files.items()}


def _python_graph(analyses: list[PyFileAnalysis]) -> list[graph.Edge]:
    modules = {a.module for a in analyses}
    edges: list[graph.Edge] = []
    for a in analyses:
        for imp in a.imports:
            if not imp.target.startswith("app"):
                continue
            targets = [f"{imp.target}.{n}" for n in imp.names if f"{imp.target}.{n}" in modules] or [imp.target]
            for t in targets:
                while t not in modules and "." in t:
                    t = t.rsplit(".", 1)[0]
                if t in modules and t != a.module:
                    edges.append(graph.Edge(a.module, t, imp.deferred))
    return edges


def _ts_graph(ts_files: dict) -> list[graph.Edge]:
    edges = []
    for name, f in ts_files.items():
        if ".test." in name:
            continue
        for imp in f["imports"]:
            if imp.get("internal"):
                edges.append(graph.Edge(name, imp["internal"], False))
    return edges


def _ts_dir_edges(edges: list[graph.Edge]) -> list[graph.Edge]:
    merged = {}
    for e in edges:
        s, t = e.source.rsplit("/", 1)[0], e.target.rsplit("/", 1)[0]
        if s != t:
            merged[(s, t)] = False
    return [graph.Edge(s, t, d) for (s, t), d in merged.items()]


def _function_rows(py: list[PyFileAnalysis], ts: dict) -> list[dict]:
    rows = []
    for a in py:
        for f in a.functions:
            rows.append({"lang": "python", "file": a.path, "name": f.qualname, "line": f.line,
                         "length": f.end_line - f.line + 1, "cyclomatic": f.cyclomatic, "cognitive": f.cognitive})
    for name, data in ts.items():
        if ".test." in name:
            continue
        for f in data["functions"]:
            rows.append({"lang": "typescript", "file": name, "name": f["name"], "line": f["line"],
                         "length": f["end_line"] - f["line"] + 1, "cyclomatic": f["cyclomatic"], "cognitive": f["cognitive"]})
    return rows


def _complexity_summary(rows: list[dict]) -> dict:
    if not rows:
        return {}
    return {
        "functions": len(rows),
        "mean_cyclomatic": round(mean(r["cyclomatic"] for r in rows), 2),
        "mean_cognitive": round(mean(r["cognitive"] for r in rows), 2),
        "max_cognitive": max(r["cognitive"] for r in rows),
        "over_cognitive_limit": sum(r["cognitive"] > COGNITIVE_LIMIT for r in rows),
        "over_cyclomatic_limit": sum(r["cyclomatic"] > CYCLOMATIC_LIMIT for r in rows),
    }


def collect(repo: Path, *, with_history: bool = True) -> dict:
    backend = repo / "backend"
    py_prod = analyze_tree(repo, backend, ["app/**/*.py", "scripts/**/*.py"], exclude=("__pycache__",))
    py_tests = analyze_tree(repo, backend, ["tests/**/*.py"], exclude=("__pycache__",))
    py_app = [a for a in py_prod if a.path.startswith("backend/app/")]
    ts_all = _ts_analysis(repo)
    ts_prod = {k: v for k, v in ts_all.items() if ".test." not in k and not k.endswith("setupTests.ts")}

    # Complexity
    rows = _function_rows(py_app, ts_prod)
    py_rows = [r for r in rows if r["lang"] == "python"]
    ts_rows = [r for r in rows if r["lang"] == "typescript"]

    # Duplication (production code only; tests legitimately repeat setup)
    py_tokens = {a.path: a.tokens for a in py_app}
    ts_tokens = {k: [tuple(t) for t in v["tokens"]] for k, v in ts_prod.items()}
    dup = {}
    for side, tokens, loc in (
        ("backend", py_tokens, {a.path: a.loc for a in py_app}),
        ("frontend", ts_tokens, {k: v["loc"] for k, v in ts_prod.items()}),
    ):
        clones = duplication.find_clones(tokens)
        dup_lines = duplication.duplicated_lines(clones)
        structural = duplication.find_clones(duplication.normalize(tokens), min_tokens=100, min_lines=10)
        structural_lines = duplication.duplicated_lines(structural)
        total = sum(loc.values())
        duplicated = sum(len(v) for v in dup_lines.values())
        dup[side] = {
            "loc": total,
            "duplicated_lines": duplicated,
            "percent": round(100 * duplicated / total, 2) if total else 0.0,
            "clones": [asdict(c) | {"lines": c.lines} for c in clones[:25]],
            "clone_count": len(clones),
            "structural_percent": round(100 * sum(len(v) for v in structural_lines.values()) / total, 2) if total else 0.0,
            "structural_clone_count": len(structural),
            "structural_clones": [asdict(c) | {"lines": c.lines} for c in structural[:15]],
            "worst_files": sorted(
                ({"file": f, "duplicated_lines": len(v), "percent": round(100 * len(v) / max(loc.get(f, 1), 1), 1)}
                 for f, v in dup_lines.items()),
                key=lambda x: -x["duplicated_lines"],
            )[:10],
        }

    # Coupling and cycles
    py_edges = _python_graph(py_app)
    py_pkg_edges = graph.collapse(py_edges, depth=2)
    ts_edges = _ts_graph(ts_prod)
    ts_dir_edges = _ts_dir_edges(ts_edges)
    coupling = {
        "backend_packages": [asdict(c) | {"instability": c.instability}
                             for c in graph.coupling(sorted({e.source for e in py_pkg_edges}), py_pkg_edges)],
        "backend_modules_top": sorted(
            (asdict(c) | {"instability": c.instability}
             for c in graph.coupling([a.module for a in py_app], py_edges)),
            key=lambda c: -(c["afferent"] + c["efferent"]),
        )[:15],
        "frontend_dirs": [asdict(c) | {"instability": c.instability}
                          for c in graph.coupling(sorted({e.source for e in ts_dir_edges}), ts_dir_edges)],
        "frontend_files_top": sorted(
            (asdict(c) | {"instability": c.instability} for c in graph.coupling(list(ts_prod), ts_edges)),
            key=lambda c: -(c["afferent"] + c["efferent"]),
        )[:10],
    }
    deferred_pairs = {(e.source, e.target) for e in py_edges if e.deferred}
    cycles = {
        "backend_modules": graph.cycles(py_edges, include_deferred=False),
        "backend_modules_incl_deferred": graph.cycles(py_edges, include_deferred=True),
        "backend_packages": graph.cycles(py_pkg_edges, include_deferred=False),
        "backend_packages_incl_deferred": graph.cycles(py_pkg_edges, include_deferred=True),
        "frontend_files": graph.cycles(ts_edges),
        "frontend_dirs": graph.cycles(ts_dir_edges),
        "deferred_import_count": len(deferred_pairs),
    }

    # Dead code and dependencies
    py_dead = deadcode.python_dead_code(py_prod, py_tests)
    ts_dead, ts_unreachable = deadcode.ts_dead_code(
        ts_all, entry_points=["frontend/src/main.tsx"],
    )
    config_texts = [p.read_text(encoding="utf-8") for p in (repo / "frontend").glob("*.ts")]
    config_texts += [p.read_text(encoding="utf-8") for p in (repo / "frontend").glob("tsconfig*.json")]
    py_migrations = analyze_tree(repo, backend, ["migrations/**/*.py"], exclude=("__pycache__",))
    unused_deps = deadcode.python_unused_dependencies(backend / "pyproject.toml", py_prod + py_tests + py_migrations)
    unused_deps += deadcode.ts_unused_dependencies(repo / "frontend" / "package.json", ts_all, config_texts)

    # Hotspots
    hotspots = []
    growth_points = []
    if with_history:
        churn = history.churn(repo)
        file_cognitive: dict[str, int] = defaultdict(int)
        file_loc: dict[str, int] = {}
        for r in rows:
            file_cognitive[r["file"]] += r["cognitive"]
        for a in py_app:
            file_loc[a.path] = a.loc
        for k, v in ts_prod.items():
            file_loc[k] = v["loc"]
        for path, cog in file_cognitive.items():
            ch = churn.get(path)
            if ch is None:
                continue
            hotspots.append({"file": path, "commits": ch.commits, "lines_changed": ch.lines_changed,
                             "cognitive": cog, "loc": file_loc.get(path, 0), "score": ch.commits * cog})
        hotspots.sort(key=lambda h: -h["score"])
        growth_points = [asdict(p) for p in history.growth(repo)]

    mutation_file = repo / "docs" / "code-health" / "mutation.json"
    mutation = json.loads(mutation_file.read_text(encoding="utf-8")) if mutation_file.exists() else {}

    return {
        "complexity": {
            "backend": _complexity_summary(py_rows),
            "frontend": _complexity_summary(ts_rows),
            "limits": {"cognitive": COGNITIVE_LIMIT, "cyclomatic": CYCLOMATIC_LIMIT},
            "top_cognitive": sorted(rows, key=lambda r: -r["cognitive"])[:20],
            "longest": sorted(rows, key=lambda r: -r["length"])[:10],
        },
        "duplication": dup,
        "coupling": coupling,
        "cycles": cycles,
        "dead_code": {
            "python": [asdict(s) for s in py_dead],
            "typescript": [asdict(s) for s in ts_dead],
            "unreachable_files": ts_unreachable,
        },
        "unused_dependencies": [asdict(d) for d in unused_deps],
        "hotspots": hotspots[:20],
        "growth": growth_points,
        "mutation": mutation,
        "size": {
            "backend_prod_loc": sum(a.loc for a in py_app),
            "backend_test_loc": sum(a.loc for a in py_tests),
            "frontend_prod_loc": sum(v["loc"] for v in ts_prod.values()),
            "frontend_test_loc": sum(v["loc"] for k, v in ts_all.items() if k not in ts_prod),
        },
    }
