"""Dead code and unused dependencies.

Python dead code is name-based (like vulture at its default confidence): a
module-level function or class, or a method, whose name is never referenced
anywhere in the scanned code -- not as a name, an attribute, an imported
name, or a string literal (to catch getattr/registry lookups). Framework
entry points are skipped: FastAPI route handlers and other decorated
callables, dunder methods, pydantic validators, test functions. A
definition referenced only from tests is reported separately as
"test-only". Name-based means false negatives (two things sharing a name
keep each other alive) but few false positives.

TypeScript dead code is export-based: an exported symbol no other file
imports, and files nothing imports (unreachable from the entry points).

Unused dependencies compare declared packages (pyproject.toml,
package.json) with what the code imports, through a distribution ->
import-name map and an allowlist of packages that are used without an
import (CLI tools, drivers loaded by URL, type packages).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .pyanalyze import PyFileAnalysis

_ENTRYPOINT_DECORATOR = re.compile(
    r"(router|app)\.(get|post|put|patch|delete|on_event|middleware|exception_handler)|"
    r"field_validator|model_validator|validator|computed_field|pytest\.fixture|"
    r"property|\w+\.setter|cached_property|lru_cache|staticmethod|classmethod|"
    r"asynccontextmanager|contextmanager"
)

# Names a framework calls by convention rather than by reference.
_CONVENTION_NAMES = {"lifespan", "main", "upgrade", "downgrade", "model_post_init", "run_migrations_online",
                     "run_migrations_offline", "Config", "setUp", "tearDown"}

# Distribution name -> import name, where they differ.
PY_IMPORT_NAMES = {
    "python-dotenv": "dotenv", "pydantic-settings": "pydantic_settings", "python-docx": "docx",
    "psycopg": "psycopg", "python-multipart": "multipart", "sentence-transformers": "sentence_transformers",
    "uvicorn": "uvicorn", "pytest-asyncio": "pytest_asyncio", "pyyaml": "yaml", "pillow": "PIL",
}
# Used without an import: loaded by URL scheme, by a server command, or a framework.
PY_IMPLICIT = {
    "psycopg": "SQLAlchemy driver selected by DATABASE_URL (postgresql+psycopg://)",
    "python-multipart": "FastAPI needs it for UploadFile/Form parsing",
    "uvicorn": "ASGI server started by the Dockerfile/start command",
    "pytest-asyncio": "pytest plugin (asyncio_mode = auto)",
    "python-dotenv": "pydantic-settings loads .env files through it (env_file=)",
    "httpx2": "",  # checked below by import name
}
TS_IMPLICIT_PREFIXES = ("@types/",)
TS_TOOLING = {"typescript": "tsc", "vite": "vite", "vitest": "vitest", "oxlint": "oxlint", "jsdom": "jsdom"}


@dataclass
class DeadSymbol:
    file: str
    line: int
    name: str
    kind: str
    status: str  # unused | test-only | export-unneeded


@dataclass
class UnusedDependency:
    manifest: str
    package: str
    note: str


def python_dead_code(prod: list[PyFileAnalysis], tests: list[PyFileAnalysis]) -> list[DeadSymbol]:
    prod_refs: set[str] = set()
    for f in prod:
        prod_refs |= f.references
        prod_refs |= f.string_literals
    test_refs: set[str] = set()
    for f in tests:
        test_refs |= f.references | f.string_literals

    out: list[DeadSymbol] = []
    for f in prod:
        if "/migrations/" in f.path:
            continue
        for d in f.definitions:
            if d.name.startswith("__") and d.name.endswith("__"):
                continue
            if d.name in _CONVENTION_NAMES or any(_ENTRYPOINT_DECORATOR.search(dec) for dec in d.decorated_by):
                continue
            if d.name in prod_refs:
                continue
            out.append(DeadSymbol(f.path, d.line, d.name, d.kind, "test-only" if d.name in test_refs else "unused"))
    return sorted(out, key=lambda s: (s.status, s.file, s.line))


def ts_dead_code(ts_files: dict, entry_points: list[str]) -> tuple[list[DeadSymbol], list[str]]:
    def is_test(name: str) -> bool:
        return ".test." in name or name.endswith("setupTests.ts")

    imported: dict[str, set[str]] = {}
    imported_by_tests: dict[str, set[str]] = {}
    graph: dict[str, set[str]] = {name: set() for name in ts_files}
    for name, f in ts_files.items():
        for imp in f["imports"]:
            target = imp.get("internal")
            if not target or target not in ts_files:
                continue  # assets (images, css) aren't analysed
            graph[name].add(target)
            bucket = imported_by_tests if is_test(name) else imported
            bucket.setdefault(target, set()).update(imp["names"])

    symbols: list[DeadSymbol] = []
    for name, f in ts_files.items():
        if is_test(name) or name in entry_points:
            continue
        used = imported.get(name, set())
        test_used = imported_by_tests.get(name, set())
        for exp in f["exports"]:
            if "*" in used or exp["name"] in used:
                continue
            # React.lazy(() => import("./X")) uses the default export dynamically.
            if exp["name"] == "default" and any(
                Path(name).stem in other["idents"] for other_name, other in ts_files.items() if other_name != name
            ):
                continue
            if "*" in test_used or exp["name"] in test_used:
                status = "test-only"
            elif f.get("ident_counts", {}).get(exp["name"], 0) > 1:
                status = "export-unneeded"  # used inside its own file; only the `export` is dead
            else:
                status = "unused"
            symbols.append(DeadSymbol(name, exp["line"], exp["name"], "export", status))

    # Reachability from the entry points (dynamic imports are recorded as idents, so
    # treat any file whose stem is mentioned in a reachable file as reachable too).
    reachable: set[str] = set()
    frontier = [e for e in entry_points if e in ts_files]
    stems = {Path(n).stem: n for n in ts_files}
    while frontier:
        cur = frontier.pop()
        if cur in reachable or cur not in ts_files:
            continue
        reachable.add(cur)
        frontier.extend(graph.get(cur, ()))
        for ident in ts_files[cur]["idents"]:
            if ident in stems and stems[ident] not in reachable:
                frontier.append(stems[ident])
    unreachable = sorted(n for n in ts_files if n not in reachable and not is_test(n))
    return sorted(symbols, key=lambda s: (s.status, s.file, s.line)), unreachable


def _dist_name(requirement: str) -> str:
    return re.split(r"[\[<>=!~ ;]", requirement, maxsplit=1)[0].strip().lower()


def python_unused_dependencies(pyproject: Path, analyses: list[PyFileAnalysis]) -> list[UnusedDependency]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    declared = [(_dist_name(r), "dependencies") for r in project.get("dependencies", [])]
    for group, reqs in project.get("optional-dependencies", {}).items():
        declared.extend((_dist_name(r), f"optional-dependencies.{group}") for r in reqs)

    top_level = {imp.target.split(".")[0] for f in analyses for imp in f.imports if imp.target}
    out: list[UnusedDependency] = []
    for dist, section in declared:
        import_name = PY_IMPORT_NAMES.get(dist, dist.replace("-", "_"))
        if import_name in top_level:
            continue
        if dist == "httpx2" and "httpx" in top_level:
            continue
        if PY_IMPLICIT.get(dist):
            continue
        out.append(UnusedDependency(f"{pyproject.name} [{section}]", dist, f"no `import {import_name}` found"))
    return out


def ts_unused_dependencies(package_json: Path, ts_files: dict, config_texts: list[str]) -> list[UnusedDependency]:
    data = json.loads(package_json.read_text(encoding="utf-8"))
    imported = set()
    for f in ts_files.values():
        for imp in f["imports"]:
            spec = imp.get("external")
            if spec:
                parts = spec.split("/")
                imported.add("/".join(parts[:2]) if spec.startswith("@") else parts[0])
    scripts = " ".join(data.get("scripts", {}).values())
    config_blob = "\n".join(config_texts)
    out: list[UnusedDependency] = []
    for section in ("dependencies", "devDependencies"):
        for pkg in data.get(section, {}):
            if pkg in imported or pkg in config_blob:
                continue
            if pkg.startswith(TS_IMPLICIT_PREFIXES):
                base = pkg.removeprefix("@types/")
                if base in imported or base in data.get("dependencies", {}) or base == "node":
                    continue
            binary = TS_TOOLING.get(pkg)
            if binary and re.search(rf"\b{re.escape(binary)}\b", scripts + config_blob):
                continue
            out.append(UnusedDependency(f"package.json [{section}]", pkg, "not imported, not used by a script or config"))
    return out
