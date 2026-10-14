"""Mutation testing for the Python backend.

Answers "would the tests notice if this logic were wrong?" -- something
line coverage can't. For a target module, each mutant changes one thing
(flip a comparison, swap and/or, +/-, True/False, nudge a number, drop a
`not`, return None instead of a value), then the module's tests run
against it. A failing run "kills" the mutant; a passing run means the
tests didn't notice ("survived"). Score = killed / (killed + survived).

Mutants are applied to a throwaway copy of backend/ in a temp directory,
never to the working tree, and run sequentially (the test suite shares one
Postgres test database, so runs can't overlap). A run timing out at 3x the
clean run's time counts as killed (it most likely hung in a loop).

Results are merged into docs/code-health/mutation.json per module, so the
report shows the latest score for every module ever measured.
"""

from __future__ import annotations

import ast
import copy
import json
import random
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

_COMPARE_SWAPS = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE,
    ast.GtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
}
_BINOP_SWAPS = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Mult}


@dataclass
class Mutant:
    index: int
    line: int
    description: str
    outcome: str = "pending"  # killed | survived | timeout | error


def _sites(tree: ast.AST) -> list[tuple[int, int, str]]:
    """(node ordinal in ast.walk order, line, description) for every mutation site."""
    sites: list[tuple[int, int, str]] = []
    in_docstring: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                in_docstring.add(id(first.value))
    for ordinal, node in enumerate(ast.walk(tree)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Compare) and type(node.ops[0]) in _COMPARE_SWAPS:
            a, b = type(node.ops[0]).__name__, _COMPARE_SWAPS[type(node.ops[0])].__name__
            sites.append((ordinal, line, f"comparison {a} -> {b}"))
        elif isinstance(node, ast.BoolOp):
            sites.append((ordinal, line, f"boolean {'and -> or' if isinstance(node.op, ast.And) else 'or -> and'}"))
        elif isinstance(node, ast.BinOp) and type(node.op) in _BINOP_SWAPS:
            sites.append((ordinal, line, f"arithmetic {type(node.op).__name__} -> {_BINOP_SWAPS[type(node.op)].__name__}"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            sites.append((ordinal, line, "drop `not`"))
        elif isinstance(node, ast.Constant) and id(node) not in in_docstring:
            if isinstance(node.value, bool):
                sites.append((ordinal, line, f"constant {node.value} -> {not node.value}"))
            elif isinstance(node.value, int) and not isinstance(node.value, bool):
                sites.append((ordinal, line, f"number {node.value} -> {node.value + 1}"))
        elif isinstance(node, ast.Return) and node.value is not None and not (
            isinstance(node.value, ast.Constant) and node.value.value is None
        ):
            sites.append((ordinal, line, "return value -> None"))
    return sites


def _apply(tree: ast.AST, ordinal: int) -> ast.AST:
    mutated = copy.deepcopy(tree)
    node = next(n for i, n in enumerate(ast.walk(mutated)) if i == ordinal)
    if isinstance(node, ast.Compare):
        node.ops[0] = _COMPARE_SWAPS[type(node.ops[0])]()
    elif isinstance(node, ast.BoolOp):
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif isinstance(node, ast.BinOp):
        node.op = _BINOP_SWAPS[type(node.op)]()
    elif isinstance(node, ast.UnaryOp):
        # Replace `not x` with `x` by turning the op into unary plus on a
        # truthiness-preserving bool() call.
        node.op = ast.UAdd()
        node.operand = ast.Call(func=ast.Name("bool", ast.Load()), args=[node.operand], keywords=[])
    elif isinstance(node, ast.Constant):
        node.value = (not node.value) if isinstance(node.value, bool) else node.value + 1
    elif isinstance(node, ast.Return):
        node.value = ast.Constant(None)
    return ast.fix_missing_locations(mutated)


def _copy_backend(backend: Path, dest: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".data", "*.egg-info", ".pytest_cache", ".venv", "node_modules")
    shutil.copytree(backend, dest, ignore=ignore)


def _run_tests(workdir: Path, tests: list[str], timeout: float | None) -> tuple[str, float]:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests],
            cwd=workdir, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return "timeout", time.monotonic() - started
    elapsed = time.monotonic() - started
    if proc.returncode == 0:
        return "survived", elapsed
    if proc.returncode in (1,):
        return "killed", elapsed
    return "error", elapsed  # collection error, usage error, ...


def run(
    backend: Path, module: str, tests: list[str], *, max_mutants: int, seed: int, results_file: Path,
    log=print,
) -> dict:
    target = backend / module
    source = target.read_text(encoding="utf-8")
    tree = ast.parse(source)
    sites = _sites(tree)
    rng = random.Random(seed)
    chosen = sorted(rng.sample(sites, min(max_mutants, len(sites))))
    log(f"{module}: {len(sites)} mutation sites, testing {len(chosen)} with {' '.join(tests)}")

    with tempfile.TemporaryDirectory(prefix="mutation-") as tmp:
        work = Path(tmp) / "backend"
        _copy_backend(backend, work)
        clean, clean_time = _run_tests(work, tests, None)
        if clean != "survived":
            raise SystemExit(f"The tests don't pass on unmutated code ({clean}); fix that first.")
        timeout = max(30.0, clean_time * 3)
        mutants: list[Mutant] = []
        for n, (ordinal, line, description) in enumerate(chosen, 1):
            (work / module).write_text(ast.unparse(_apply(tree, ordinal)), encoding="utf-8")
            outcome, _ = _run_tests(work, tests, timeout)
            mutants.append(Mutant(ordinal, line, description, outcome))
            log(f"  [{n}/{len(chosen)}] line {line}: {description} -> {outcome}")
        (work / module).write_text(source, encoding="utf-8")

    killed = sum(m.outcome in ("killed", "timeout") for m in mutants)
    survived = sum(m.outcome == "survived" for m in mutants)
    summary = {
        "module": module,
        "tests": tests,
        "sites": len(sites),
        "tested": len(mutants),
        "killed": killed,
        "survived": survived,
        "errors": sum(m.outcome == "error" for m in mutants),
        "score": round(killed / (killed + survived), 3) if killed + survived else None,
        "measured_at": time.strftime("%Y-%m-%d"),
        "survivors": [asdict(m) for m in mutants if m.outcome == "survived"],
    }
    existing = json.loads(results_file.read_text(encoding="utf-8")) if results_file.exists() else {}
    existing[module] = summary
    results_file.parent.mkdir(parents=True, exist_ok=True)
    results_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return summary
