"""Generators for the wiki's generated blocks. Each returns Markdown built
from the code itself -- the SQLAlchemy models, the FastAPI routes, the LLM
call sites, the import graph, the epic files -- so the facts in the wiki
can't drift from the code without `python -m tools.docs check` noticing.

Every generator is a pure function of the repository; output is sorted
and free of timestamps so regenerating an unchanged repo changes nothing.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


def _backend_on_path() -> None:
    os.environ.setdefault("OPENROUTER_API_KEY", "docs-generation-only")
    os.environ.setdefault("LLM_USAGE_LOG_ENABLED", "false")
    os.environ.setdefault("JOB_WORKER_ENABLED", "false")
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


# ---- data model -------------------------------------------------------------

# Table -> domain, by the epic that introduced it. Unlisted tables land in
# "Other" -- `check` flags them so this map stays complete.
DOMAINS: dict[str, list[str]] = {
    "Process capture (Epics 1-6, 11)": [
        "processes", "documents", "actors", "process_elements", "source_refs", "process_flows",
        "document_embeddings", "process_schema_changes", "bpmn_drafts", "chat_messages", "versions", "gap_findings",
    ],
    "Blueprint and agents (Epics 7, 12-15, 20, 23)": [
        "blueprint_overlays", "agent_artifacts", "registry_entries", "agent_publications",
    ],
    "Digital twin runs (Epics 14, 16-22)": [
        "twin_tool_schemas", "twin_baselines", "twin_environments", "twin_scenarios", "twin_runs",
        "orchestration_scenarios", "orchestration_runs", "simulation_runs", "regression_suites", "regression_suite_runs",
    ],
    "Organization context (Epics 27-31)": [
        "organizations", "context_entities", "context_entity_versions", "context_documents", "policy_passages",
        "context_packs", "process_system_links", "connectors", "connector_runs", "user_business_units",
        "context_access_log",
    ],
    "Ground truth and volume (Epics 32-35)": [
        "labelled_datasets", "dataset_rows", "decision_rules", "metamorphic_relations", "judge_rubrics",
        "batch_runs", "batch_schedules",
    ],
    "Process twin (Epic 36)": ["process_operating_models", "process_twin_batches"],
    "Identity and access (Epics 9-10, 37, 38)": [
        "users", "user_roles", "user_identities", "sessions", "session_tokens", "oidc_login_requests",
        "scim_tokens", "directory_groups", "directory_group_members", "group_role_mappings", "tenant_identity",
        "support_grants", "support_entries", "contributions", "approvals", "audit_events",
    ],
    "Platform (Epics 31, 38)": [
        "jobs", "tenants", "tenant_domains", "operators", "operator_sessions", "operator_login_requests",
        "platform_audit_events",
    ],
}


def _metadata():
    _backend_on_path()
    from app.db.models import Base

    return Base.metadata


def _type(col) -> str:
    name = type(col.type).__name__
    return {"String": "string", "Text": "text", "Integer": "int", "BigInteger": "bigint", "Boolean": "bool",
            "DateTime": "datetime", "Float": "float", "JSON": "json", "Vector": "vector"}.get(name, name.lower())


def unisolated_tables() -> list[str]:
    """Epic 38, US38.2: tables that are neither platform tables nor carry
    the tenant column -- row-level security can't apply to them."""
    _backend_on_path()
    from app.db.models import NON_TENANT_TABLES

    md = _metadata()
    return sorted(t for t in md.tables if t not in NON_TENANT_TABLES and "tenant_id" not in md.tables[t].c)


def unmapped_tables() -> list[str]:
    mapped = {t for tables in DOMAINS.values() for t in tables}
    return sorted(t for t in _metadata().tables if t not in mapped)


def er_diagrams() -> str:
    """One Mermaid ER diagram per domain: every table with its key columns,
    and every foreign key as a relationship (cross-domain targets shown as
    stubs so each diagram stays readable)."""
    md = _metadata()
    out: list[str] = []
    for domain, tables in DOMAINS.items():
        present = [t for t in tables if t in md.tables]
        if not present:
            continue
        out += [f"### {domain}", "", "```mermaid", "erDiagram"]
        stubs: set[str] = set()
        rels: list[str] = []
        for name in present:
            table = md.tables[name]
            out.append(f"    {name} {{")
            for col in table.columns:
                if col.name == "tenant_id" and name != "tenant_domains":
                    continue  # on every tenant table (Epic 38); drawn once, in Platform
                keys = []
                if col.primary_key:
                    keys.append("PK")
                if col.foreign_keys:
                    keys.append("FK")
                if keys or _type(col) == "json" or col.name in ("status", "kind", "version", "name"):
                    out.append(f"        {_type(col)} {col.name}{' ' + ','.join(keys) if keys else ''}")
            out.append("    }")
            for fk in sorted(table.foreign_keys, key=lambda f: f.parent.name):
                target = fk.column.table.name
                if target == "users":
                    continue  # every audited row points at users; drawn once in Identity and access
                if target == "tenants" and name != "tenant_domains":
                    continue  # every tenant row points at tenants (Epic 38)
                if target not in present:
                    stubs.add(target)
                card = "o|" if fk.parent.nullable else "||"
                rels.append(f'    {target} {card}--o{{ {name} : "{fk.parent.name}"')
        for stub in sorted(stubs):
            out.append(f"    {stub} {{ }}")
        out += rels + ["```", ""]
    return "\n".join(out).rstrip() + "\n"


def table_catalogue() -> str:
    md = _metadata()
    _backend_on_path()
    from app.db import models

    docs = {cls.__tablename__: (cls.__name__, (cls.__doc__ or "").strip())
            for cls in vars(models).values() if isinstance(cls, type) and hasattr(cls, "__tablename__")}
    out: list[str] = []
    for domain, tables in DOMAINS.items():
        out += [f"### {domain}", ""]
        for name in tables:
            if name not in md.tables:
                continue
            table = md.tables[name]
            cls, doc = docs.get(name, ("", ""))
            first = re.sub(r"\s+", " ", doc.split("\n\n")[0]) if doc else ""
            out += [f"#### `{name}` ({cls})", "", first, ""] if first else [f"#### `{name}` ({cls})", ""]
            out += ["| Column | Type | Null | Notes |", "|---|---|---|---|"]
            for col in table.columns:
                if col.name == "tenant_id" and name != "tenant_domains":
                    continue  # on every tenant table; see "How the data is shaped"
                notes = []
                if col.primary_key:
                    notes.append("primary key")
                for fk in col.foreign_keys:
                    notes.append(f"-> `{fk.column.table.name}.{fk.column.name}`"
                                 + (f" ({fk.ondelete.lower()})" if fk.ondelete else ""))
                if col.index:
                    notes.append("indexed")
                out.append(f"| `{col.name}` | {_type(col)} | {'yes' if col.nullable else 'no'} | {'; '.join(notes)} |")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def schema_stats() -> str:
    md = _metadata()
    cols = sum(len(t.columns) for t in md.tables.values())
    json_cols = sum(1 for t in md.tables.values() for c in t.columns if _type(c) == "json")
    fks = sum(len(t.foreign_keys) for t in md.tables.values())
    migrations = len(list((BACKEND / "migrations" / "versions").glob("*.py")))
    return (f"**{len(md.tables)} tables, {cols} columns ({json_cols} JSON), {fks} foreign keys, "
            f"{migrations} Alembic migrations.**\n")


# ---- API --------------------------------------------------------------------


def _route_access() -> dict[tuple[str, str], str]:
    """(path, METHOD) -> the permission its guard requires (Epic 37's
    `require(...)`), "signed in" for any session, or "public"."""
    from fastapi.routing import APIRoute, iter_route_contexts

    from app.main import app

    access = {}
    # Not app.routes: FastAPI 0.141 wraps included routers there.
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute):
            continue
        calls, stack = [], list(route.dependant.dependencies)
        while stack:
            dep = stack.pop()
            calls.append(dep.call)
            stack.extend(dep.dependencies)
        permission = next((c.required_permission for c in calls if getattr(c, "required_permission", None)), None)
        signed_in = any(getattr(c, "__name__", "") in ("get_current_session", "get_current_user") for c in calls)
        scim_token = any(getattr(c, "__name__", "") == "scim_client" for c in calls)
        label = f"`{permission.value}`" if permission else (
            "signed in" if signed_in else "SCIM token" if scim_token else "public"
        )
        for method in route.methods:
            access[(route.path, method)] = label
    return access


def api_catalogue() -> str:
    """From the OpenAPI document FastAPI serves, so it's exactly what the API exposes."""
    _backend_on_path()
    from app.main import app

    route_access = _route_access()
    by_tag: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    for path, methods in app.openapi()["paths"].items():
        for method, op in methods.items():
            access = route_access.get((path, method.upper()), "public")
            doc = re.sub(r"\s+", " ", (op.get("description") or "").split("\n\n")[0])
            by_tag[(op.get("tags") or ["other"])[0]].append((path, method.upper(), access, doc[:140]))
    total = sum(len(v) for v in by_tag.values())
    out = [
        f"**{total} endpoints.** Access: a `permission` (granted by roles, checked against the process's "
        "business unit -- see Glossary); *signed in* needs any session; *SCIM token* needs a provisioning token "
        "from the Directory page; *public* needs none.",
        "",
    ]
    for tag in sorted(by_tag):
        out += [f"### {tag}", "", "| Method | Path | Access | What it does |", "|---|---|---|---|"]
        for path, method, access, doc in sorted(by_tag[tag]):
            out.append(f"| {method} | `{path}` | {access} | {doc.replace('|', '/')} |")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---- prompts ----------------------------------------------------------------

_PROMPTISH = re.compile(r"prompt|instruction|system|template|_SYSTEM|_PROMPT|messages", re.IGNORECASE)


def _module_defs(tree: ast.Module) -> dict[str, ast.AST]:
    defs: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defs[target.id] = node
    return defs


def _imports(tree: ast.Module, module_path: Path) -> dict[str, Path]:
    """Name -> source file, for names imported from sibling/app modules."""
    out: dict[str, Path] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module is not None or isinstance(node, ast.ImportFrom) and node.level:
            if node.level:
                base = module_path.parent
                for _ in range(node.level - 1):
                    base = base.parent
                target = base / Path(*(node.module or "").split(".")) if node.module else base
            elif node.module.startswith("app."):
                target = BACKEND / Path(*node.module.split("."))
            else:
                continue
            file = target.with_suffix(".py")
            if file.exists():
                for alias in node.names:
                    out[alias.asname or alias.name] = file
    return out


def llm_call_sites() -> list[dict]:
    sites = []
    for path in sorted((BACKEND / "app").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if ".complete(" not in source:
            continue
        tree = ast.parse(source)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                # `llm.complete(..., operation="x")` directly, or through a
                # local helper that forwards `operation=` (twin/test_builder.py).
                if not isinstance(call, ast.Call):
                    continue
                kw = {k.arg: k.value for k in call.keywords}
                op = kw.get("operation")
                if not (isinstance(op, ast.Constant) and isinstance(op.value, str)):
                    continue
                helper = ast.unparse(call.func)
                sites.append({
                    "operation": op.value, "path": path, "function": fn, "tree": tree,
                    "line": call.lineno,
                    "json": "response_format" in kw or "json" in helper.lower(),
                    "helper": helper,
                    "tools": "tools" in kw,
                    "max_tokens": ast.unparse(kw["max_tokens"]) if "max_tokens" in kw else "default",
                    "model": ast.unparse(kw["model"]) if "model" in kw else "default (routing policy)",
                })
    # One entry per operation per function (some functions call twice).
    seen, unique = set(), []
    for s in sites:
        key = (s["operation"], s["path"], s["function"].name)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return sorted(unique, key=lambda s: (s["operation"], str(s["path"])))


_PURPOSE = {
    "blueprint_evaluation": "Judge every step of a finalized process for agent automation and specify the agent",
    "chat_edit": "Turn a chat instruction into a diagram change (add/amend/delete a node or flow)",
    "policy_extraction": "Extract obligations, controls and approval rules from an uploaded policy document",
    "gap_analysis": "Find gaps and ambiguities in an extracted process before diagramming",
    "document_extraction": "Extract the process (actors, steps, decisions, flows) from a document or image",
    "oracle_rule_structuring": "Turn a gateway's condition text into an executable rule expression",
    "oracle_judge": "Grade a free-text output against a calibrated rubric (LLM judge)",
    "twin_run": "The agent itself, running in the twin with tools and the review tool",
    "orchestration_data_adapter": "Reshape the case file into the next agent's input fields",
    "twin_tool_schema_inference": "Invent a callable tool schema for each system an agent needs",
    "twin_system_simulation": "Play a system (Proxy mode) and fabricate a plausible response",
    "twin_builder_suggest": "Draft test situations from the blueprint (guided test builder)",
    "twin_builder_describe": "Turn a plain-language description into a test situation",
    "twin_builder_personas": "Cast reviewer personas for a test",
}


def prompt_catalogue() -> str:
    sites = llm_call_sites()
    out = ["| Operation | Purpose | Where | Output | Tools | Max tokens |", "|---|---|---|---|---|---|"]
    for s in sites:
        rel = s["path"].relative_to(REPO).as_posix()
        out.append(
            f"| `{s['operation']}` | {_PURPOSE.get(s['operation'], '')} | `{rel}` `{s['function'].name}()` | "
            f"{'JSON' if s['json'] else 'text / tool calls'} | {'yes' if s['tools'] else 'no'} | {s['max_tokens']} |"
        )
    out += ["", f"**{len(sites)} call sites, {len({s['operation'] for s in sites})} operations.** "
            "Every call goes through `LLMClient.complete` (`backend/app/llm/client.py`), which applies the "
            "business unit's model policy (residency, PII redaction, provider allow-list) and logs usage and cost "
            "per operation.", ""]

    # The prompt text, as code: each call site's function plus every
    # prompt-like helper or constant it uses (same module or imported).
    for s in sites:
        rel = s["path"].relative_to(REPO).as_posix()
        tree, fn = s["tree"], s["function"]
        source = s["path"].read_text(encoding="utf-8")
        defs = _module_defs(tree)
        imported = _imports(tree, s["path"])
        used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        blocks = [(f"{rel} -- {fn.name}()", ast.get_source_segment(source, fn) or "")]
        helper = s["helper"]
        if helper in defs and helper != fn.name:
            blocks.append((f"{rel} -- {helper}()", ast.get_source_segment(source, defs[helper]) or ""))
        for name in sorted(used):
            if not _PROMPTISH.search(name) or name == fn.name:
                continue
            if name in defs:
                blocks.append((f"{rel} -- {name}", ast.get_source_segment(source, defs[name]) or ""))
            elif name in imported:
                other = imported[name]
                other_src = other.read_text(encoding="utf-8")
                other_defs = _module_defs(ast.parse(other_src))
                if name in other_defs:
                    blocks.append((f"{other.relative_to(REPO).as_posix()} -- {name}",
                                   ast.get_source_segment(other_src, other_defs[name]) or ""))
        out += [f"### `{s['operation']}`", "", _PURPOSE.get(s["operation"], ""), ""]
        for title, code in blocks:
            out += ["<details>", f"<summary><code>{title}</code></summary>", "", "```python", code.rstrip(), "```", "", "</details>", ""]
    return "\n".join(out).rstrip() + "\n"


# ---- architecture -----------------------------------------------------------


def module_graph() -> str:
    """Backend package dependencies (import-time edges solid, function-level
    'lazy' imports dashed), from the same graph code health uses."""
    sys.path.insert(0, str(REPO))
    from tools.code_health import graph
    from tools.code_health.collect import _python_graph
    from tools.code_health.pyanalyze import analyze_tree

    analyses = analyze_tree(REPO, BACKEND, ["app/**/*.py"], exclude=("__pycache__",))
    edges = graph.collapse(_python_graph(analyses), depth=2)
    skip = {"app.ids", "app.config", "app.store", "app.request_context", "app.main"}
    lines = ["```mermaid", "flowchart LR"]
    for e in sorted(edges, key=lambda e: (e.source, e.target)):
        if e.source in skip or e.target in skip:
            continue
        s, t = e.source.removeprefix("app."), e.target.removeprefix("app.")
        lines.append(f"    {s} {'-.->' if e.deferred else '-->'} {t}")
    lines += ["```", "", "Solid: imported at module load. Dashed: imported inside a function only (a 'lazy' import, "
              "usually there to dodge an import cycle). Omitted for readability: `ids`, `config`, `store`, "
              "`request_context` (used everywhere) and `main` (imports every router)."]
    return "\n".join(lines) + "\n"


def module_inventory() -> str:
    rows = []
    for pkg in sorted(p for p in (BACKEND / "app").iterdir() if p.is_dir() and (p / "__init__.py").exists()):
        files = [f for f in pkg.glob("*.py")]
        loc = sum(sum(1 for line in f.read_text(encoding="utf-8").splitlines() if line.strip()) for f in files)
        init = (pkg / "__init__.py").read_text(encoding="utf-8")
        doc = ast.get_docstring(ast.parse(init)) or ""
        if not doc:
            first = next((f for f in sorted(files) if f.name != "__init__.py"), None)
            doc = ast.get_docstring(ast.parse(first.read_text(encoding="utf-8"))) if first else ""
        summary = re.sub(r"\s+", " ", (doc or "").split("\n\n")[0])[:160]
        rows.append(f"| `app/{pkg.name}` | {len(files)} | {loc:,} | {summary.replace('|', '/')} |")
    return "\n".join(["| Package | Files | Lines | Purpose (from its docstring) |", "|---|---|---|---|", *rows]) + "\n"


# ---- planning ---------------------------------------------------------------


def epic_index() -> str:
    rows = []
    for path in sorted((REPO / "planning" / "epics").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title = text.splitlines()[0].lstrip("# ").strip()
        status_match = re.search(r"\*\*Status:?\*?\*?:?\s*(.*?)(\*\*|\n\n)", text[:3000], re.DOTALL)
        status = re.sub(r"\s+", " ", status_match.group(1)).strip(" *.") if status_match else "not stated"
        status = status if len(status) <= 90 else status[:87].rsplit(" ", 1)[0] + "..."
        stories = len(re.findall(r"^US\d+\.\d+\s+--", text, re.MULTILINE))
        rows.append(f"| [{title}](../../planning/epics/{path.name}) | {stories} | {status} |")
    return "\n".join(["| Epic | Stories | Status (from the epic file) |", "|---|---|---|", *rows]) + "\n"


def code_health_summary() -> str:
    report = REPO / "docs" / "code-health" / "report.json"
    if not report.exists():
        return "_Run `python -m tools.code_health report` to measure._\n"
    data = json.loads(report.read_text(encoding="utf-8"))
    cx, dup, cyc = data["complexity"], data["duplication"], data["cycles"]
    mut = [m for m in data["mutation"].values() if m.get("score") is not None]
    rows = [
        ("Production lines (backend / frontend)", f"{data['size']['backend_prod_loc']:,} / {data['size']['frontend_prod_loc']:,}"),
        ("Functions over cognitive complexity 15", f"{cx['backend']['over_cognitive_limit']} / {cx['frontend']['over_cognitive_limit']}"),
        ("Most complex function", f"`{cx['top_cognitive'][0]['name']}` ({cx['top_cognitive'][0]['cognitive']})"),
        ("Duplication, exact / structural (backend)", f"{dup['backend']['percent']}% / {dup['backend']['structural_percent']}%"),
        ("Import cycles (import-time / incl. lazy imports)", f"{len(cyc['backend_modules'])} / {len(cyc['backend_modules_incl_deferred'])}"),
        ("Top hotspot", f"`{data['hotspots'][0]['file']}`" if data["hotspots"] else "-"),
        ("Mutation score (modules measured)", ", ".join(f"`{m['module'].split('/')[-1]}` {m['score']:.0%}" for m in mut) or "not run"),
    ]
    return "\n".join(["| Measure | Value |", "|---|---|", *[f"| {a} | {b} |" for a, b in rows]]) + \
        "\n\nFull report: [docs/code-health/report.md](../code-health/report.md).\n"


GENERATORS = {
    "er-diagrams": er_diagrams,
    "table-catalogue": table_catalogue,
    "schema-stats": schema_stats,
    "api-catalogue": api_catalogue,
    "prompt-catalogue": prompt_catalogue,
    "module-graph": module_graph,
    "module-inventory": module_inventory,
    "epic-index": epic_index,
    "code-health-summary": code_health_summary,
}
