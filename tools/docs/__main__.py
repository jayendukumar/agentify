"""Keeps the wiki (docs/wiki) in step with the code.

Wiki pages are hand-written, with generated blocks between markers:

    <!-- BEGIN GENERATED: api-catalogue -->
    ...regenerated from the code; don't edit by hand...
    <!-- END GENERATED: api-catalogue -->

    python -m tools.docs sync [--with-health]   # regenerate every block (and the code-health report)
    python -m tools.docs check                  # exit 1 if any block is stale, a link is broken,
                                                # or a table/epic/LLM call site isn't documented
    python -m tools.docs status                 # list blocks and whether each is current
    python -m tools.docs serve [--port N]       # read the wiki in a browser, with navigation
    python -m tools.docs build [--out DIR]      # a static HTML copy (default docs/site)

`check` is what a pre-commit hook or CI runs (see tools/docs/README.md);
`sync` is the "update the docs now" button. Narrative sections (the
critical review, design rationale) are not generated: `check` reports when
their source epics or code changed after the page's "Last reviewed" date so
a person (or Claude, via /update-docs) revisits them.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from .generators import GENERATORS, REPO, llm_call_sites, unisolated_tables, unmapped_tables

WIKI = REPO / "docs" / "wiki"
BLOCK = re.compile(r"(<!-- BEGIN GENERATED: ([\w-]+) -->\n)(.*?)(<!-- END GENERATED: \2 -->)", re.DOTALL)
LINK = re.compile(r"\]\(([^)#\s]+)(#[^)]*)?\)")
REVIEWED = re.compile(r"Last reviewed:\s*(\d{4}-\d{2}-\d{2})")


def _render_all() -> dict[str, str]:
    cache: dict[str, str] = {}
    for page in sorted(WIKI.glob("*.md")):
        for _, key, _, _ in BLOCK.findall(page.read_text(encoding="utf-8")):
            if key not in cache:
                if key not in GENERATORS:
                    raise SystemExit(f"{page.name}: unknown generated block '{key}'")
                cache[key] = GENERATORS[key]()
    return cache


def _apply(text: str, rendered: dict[str, str]) -> str:
    return BLOCK.sub(lambda m: m.group(1) + rendered[m.group(2)] + m.group(4), text)


def cmd_sync(args) -> int:
    if args.with_health:
        subprocess.run([sys.executable, "-m", "tools.code_health", "report"], cwd=REPO, check=True)
    rendered = _render_all()
    changed = []
    for page in sorted(WIKI.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        new = _apply(text, rendered)
        if new != text:
            page.write_text(new, encoding="utf-8")
            changed.append(page.name)
    print(f"Updated: {', '.join(changed)}" if changed else "Wiki already up to date.")
    return 0


def _broken_links() -> list[str]:
    broken = []
    for page in sorted(WIKI.glob("*.md")):
        for target, _ in LINK.findall(page.read_text(encoding="utf-8")):
            if re.match(r"^[a-z]+:", target):
                continue
            if not (page.parent / target).resolve().exists():
                broken.append(f"{page.name}: broken link to {target}")
    return broken


def _undocumented() -> list[str]:
    problems = [f"table '{t}' isn't in a domain in tools/docs/generators.py DOMAINS" for t in unmapped_tables()]
    problems += [f"table '{t}' has no tenant_id: add TenantScoped (Epic 38) or list it in PLATFORM_TABLES"
                 for t in unisolated_tables()]
    from .generators import _PURPOSE

    problems += [f"LLM operation '{s['operation']}' has no purpose in tools/docs/generators.py _PURPOSE"
                 for s in llm_call_sites() if s["operation"] not in _PURPOSE]
    index = (REPO / "planning" / "00-index.md").read_text(encoding="utf-8")
    for epic in sorted((REPO / "planning" / "epics").glob("*.md")):
        if epic.name not in index:
            problems.append(f"planning/epics/{epic.name} isn't linked from planning/00-index.md")
    return problems


def _review_due() -> list[str]:
    """Hand-written pages whose sources changed after they were last reviewed."""
    due = []
    for page in sorted(WIKI.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        reviewed = REVIEWED.search(text)
        sources = re.search(r"Sources:\s*(.+)", text)
        if not reviewed or not sources:
            continue
        paths = [p.strip(" `") for p in sources.group(1).split(",")]
        log = subprocess.run(
            ["git", "-C", str(REPO), "log", "-1", "--format=%ad", "--date=short", f"--since={reviewed.group(1)} 23:59", "--", *paths],
            capture_output=True, text=True,
        ).stdout.strip()
        if log:
            due.append(f"{page.name}: its sources changed on {log}, after it was last reviewed ({reviewed.group(1)})")
    return due


def cmd_check(args) -> int:
    rendered = _render_all()
    stale = []
    for page in sorted(WIKI.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        for _, key, body, _ in BLOCK.findall(text):
            if body != rendered[key]:
                stale.append(f"{page.name}: generated block '{key}' is stale")
    problems = stale + _broken_links() + _undocumented()
    for line in problems:
        print(f"FAIL  {line}")
    for line in _review_due():
        print(f"REVIEW  {line}")
    if stale:
        print("Run `python -m tools.docs sync` to regenerate.")
    if not problems:
        print("Docs: generated blocks current, links resolve, every table/epic/LLM call documented.")
    return 1 if problems else 0


def cmd_status(args) -> int:
    rendered = _render_all()
    for page in sorted(WIKI.glob("*.md")):
        text = page.read_text(encoding="utf-8")
        blocks = BLOCK.findall(text)
        reviewed = REVIEWED.search(text)
        note = f" (last reviewed {reviewed.group(1)})" if reviewed else ""
        print(f"{page.name}{note}")
        for _, key, body, _ in blocks:
            print(f"    {key:22} {'current' if body == rendered[key] else 'STALE'}")
    return 0


def cmd_serve(args) -> int:
    from .site import serve

    serve(args.port, open_browser=not args.no_open)
    return 0


def cmd_build(args) -> int:
    from .site import build

    out = Path(args.out).resolve()
    pages = build(out)
    print(f"Wrote {len(pages)} pages to {out} -- open {out / 'index.html'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.docs")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sync")
    p.add_argument("--with-health", action="store_true", help="re-measure code health first")
    p.set_defaults(func=cmd_sync)
    sub.add_parser("check").set_defaults(func=cmd_check)
    sub.add_parser("status").set_defaults(func=cmd_status)
    p = sub.add_parser("serve", help="read the wiki in a browser")
    p.add_argument("--port", type=int, default=8090)
    p.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("build", help="write a static HTML copy of the wiki")
    p.add_argument("--out", default=str(WIKI.parent / "site"))
    p.set_defaults(func=cmd_build)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
