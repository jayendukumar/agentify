"""Renders a collect() snapshot as Markdown (docs/code-health/report.md)."""

from __future__ import annotations

from datetime import date


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _status(ok: bool | None) -> str:
    return {True: "OK", False: "**Attention**", None: "n/a"}[ok]


def _per_1k(value: float, loc: int) -> str:
    return f"{1000 * value / loc:.1f}" if loc else "-"


def render(data: dict, thresholds: dict) -> str:
    cx, dup, cyc = data["complexity"], data["duplication"], data["cycles"]
    size = data["size"]
    t = thresholds
    mutation_scores = [m["score"] for m in data["mutation"].values() if m.get("score") is not None]
    mean_mutation = round(sum(mutation_scores) / len(mutation_scores), 2) if mutation_scores else None
    lines: list[str] = []
    add = lines.append

    add("# Code health report")
    add("")
    add(f"_Generated {date.today().isoformat()} by `python -m tools.code_health report`. Do not edit by hand: "
        "rerun the command (or `python -m tools.docs sync`) to refresh it. How each measure is computed is "
        "described in [tools/code_health/README.md](../../tools/code_health/README.md)._")
    add("")
    add("## Scorecard")
    add("")
    rows = [
        ["Production code (non-blank lines)", f"{size['backend_prod_loc']:,}", f"{size['frontend_prod_loc']:,}", "-", ""],
        ["Test code (non-blank lines)", f"{size['backend_test_loc']:,}", f"{size['frontend_test_loc']:,}", "-", ""],
        ["Functions measured", cx["backend"].get("functions"), cx["frontend"].get("functions"), "-", ""],
        ["Mean cognitive complexity / function", cx["backend"].get("mean_cognitive"), cx["frontend"].get("mean_cognitive"),
         f"<= {t['mean_cognitive_max']}",
         _status(max(cx["backend"].get("mean_cognitive", 0), cx["frontend"].get("mean_cognitive", 0)) <= t["mean_cognitive_max"])],
        [f"Functions over cognitive {cx['limits']['cognitive']}", cx["backend"].get("over_cognitive_limit"),
         cx["frontend"].get("over_cognitive_limit"), "ratchet: must not rise", ""],
        [f"Functions over cyclomatic {cx['limits']['cyclomatic']}", cx["backend"].get("over_cyclomatic_limit"),
         cx["frontend"].get("over_cyclomatic_limit"), "ratchet: must not rise", ""],
        ["Duplicated lines", f"{dup['backend']['percent']}%", f"{dup['frontend']['percent']}%",
         f"<= {t['duplication_percent_max']}%",
         _status(max(dup['backend']['percent'], dup['frontend']['percent']) <= t["duplication_percent_max"])],
        ["Structural duplication (renamed copies)", f"{dup['backend']['structural_percent']}%",
         f"{dup['frontend']['structural_percent']}%", "ratchet", ""],
        ["Import cycles (module level, import-time)", len(cyc["backend_modules"]), len(cyc["frontend_files"]), "0",
         _status(len(cyc["backend_modules"]) + len(cyc["frontend_files"]) == 0)],
        ["Package cycles (incl. lazy imports)", len(cyc["backend_packages_incl_deferred"]), len(cyc["frontend_dirs"]),
         "ratchet", ""],
        ["Unused definitions / exports", sum(d["status"] == "unused" for d in data["dead_code"]["python"]),
         sum(d["status"] == "unused" for d in data["dead_code"]["typescript"]), "ratchet", ""],
        ["Unused dependencies", sum("pyproject" in d["manifest"] for d in data["unused_dependencies"]),
         sum("package.json" in d["manifest"] for d in data["unused_dependencies"]), "0",
         _status(not data["unused_dependencies"])],
        ["Mutation score (mean of measured modules)", mean_mutation if mean_mutation is not None else "not run", "-",
         f">= {t['mutation_score_min']}",
         _status(None if mean_mutation is None else mean_mutation >= t["mutation_score_min"])],
    ]
    add(_table(["Measure", "Backend (Python)", "Frontend (TS/React)", "Target", "Status"], rows))
    add("")

    # Complexity
    add("## 1. Complexity")
    add("")
    add("Cognitive complexity measures how hard a function is to *read* (nesting is penalised); cyclomatic "
        "complexity counts independent paths, i.e. the minimum number of tests to cover every branch. "
        f"Limits: cognitive {cx['limits']['cognitive']}, cyclomatic {cx['limits']['cyclomatic']}.")
    add("")
    add("### Most complex functions")
    add("")
    add(_table(["Function", "File", "Cognitive", "Cyclomatic", "Lines"],
               [[f"`{r['name']}`", f"`{r['file']}:{r['line']}`", r["cognitive"], r["cyclomatic"], r["length"]]
                for r in cx["top_cognitive"][:15]]))
    add("")
    add("### Longest functions")
    add("")
    add(_table(["Function", "File", "Lines", "Cognitive"],
               [[f"`{r['name']}`", f"`{r['file']}:{r['line']}`", r["length"], r["cognitive"]] for r in cx["longest"]]))
    add("")

    # Duplication
    add("## 2. Duplication")
    add("")
    add("Copy-paste clones of at least 70 tokens and 6 lines, ignoring whitespace and comments, in production code.")
    add("")
    for side in ("backend", "frontend"):
        d = dup[side]
        add(f"**{side.title()}:** {d['duplicated_lines']:,} of {d['loc']:,} lines ({d['percent']}%) sit in "
            f"{d['clone_count']} exact clone pairs; {d['structural_percent']}% sit in {d['structural_clone_count']} "
            "structural clones (same shape with identifiers and literals renamed, at least 100 tokens and 10 lines).")
        add("")
        if d["clones"]:
            add(_table(["Tokens", "Lines", "First copy", "Second copy"],
                       [[c["tokens"], c["lines"], f"`{c['a_file']}:{c['a_start']}-{c['a_end']}`",
                         f"`{c['b_file']}:{c['b_start']}-{c['b_end']}`"] for c in d["clones"][:8]]))
            add("")
        if d["structural_clones"]:
            add(f"Largest structural clones ({side}):")
            add("")
            add(_table(["Tokens", "Lines", "First copy", "Second copy"],
                       [[c["tokens"], c["lines"], f"`{c['a_file']}:{c['a_start']}-{c['a_end']}`",
                         f"`{c['b_file']}:{c['b_start']}-{c['b_end']}`"] for c in d["structural_clones"][:8]]))
            add("")

    # Hotspots
    add("## 3. Hotspots (churn x complexity)")
    add("")
    add("Files that are both complicated and frequently changed. Score = commits touching the file x the sum of "
        "its functions' cognitive complexity. Refactoring the top of this list pays back fastest.")
    add("")
    if data["hotspots"]:
        add(_table(["File", "Commits", "Lines changed", "Cognitive (sum)", "Lines", "Score"],
                   [[f"`{h['file']}`", h["commits"], f"{h['lines_changed']:,}", h["cognitive"], h["loc"], f"{h['score']:,}"]
                    for h in data["hotspots"][:15]]))
    else:
        add("_History not analysed (run without `--no-history`)._")
    add("")

    # Coupling
    add("## 4. Coupling and dependency cycles")
    add("")
    add("Ca = how many internal packages depend on this one; Ce = how many it depends on; "
        "instability I = Ce/(Ca+Ce). Packages near I=0 should be stable foundations; near I=1 should be leaves.")
    add("")
    add("### Backend packages")
    add("")
    add(_table(["Package", "Ca (in)", "Ce (out)", "Instability"],
               [[f"`{c['node']}`", c["afferent"], c["efferent"], c["instability"]]
                for c in sorted(data["coupling"]["backend_packages"], key=lambda c: -(c["afferent"] + c["efferent"]))]))
    add("")
    add("### Most-coupled backend modules")
    add("")
    add(_table(["Module", "Ca (in)", "Ce (out)", "Instability"],
               [[f"`{c['node']}`", c["afferent"], c["efferent"], c["instability"]] for c in data["coupling"]["backend_modules_top"][:10]]))
    add("")
    add("### Frontend directories")
    add("")
    add(_table(["Directory", "Ca (in)", "Ce (out)", "Instability"],
               [[f"`{c['node']}`", c["afferent"], c["efferent"], c["instability"]] for c in data["coupling"]["frontend_dirs"]]))
    add("")
    add("### Cycles")
    add("")
    add(f"- Backend module cycles at import time: **{len(cyc['backend_modules'])}**")
    add(f"- Backend module cycles including function-level (lazy) imports: **{len(cyc['backend_modules_incl_deferred'])}** "
        f"({cyc['deferred_import_count']} lazy import edges in total -- usually a sign a cycle was dodged rather than removed)")
    add(f"- Backend package cycles including lazy imports: **{len(cyc['backend_packages_incl_deferred'])}**")
    add(f"- Frontend file cycles: **{len(cyc['frontend_files'])}**, directory cycles: **{len(cyc['frontend_dirs'])}**")
    add("")
    for title, key in (("Backend package cycles (incl. lazy imports)", "backend_packages_incl_deferred"),
                       ("Backend module cycles (incl. lazy imports)", "backend_modules_incl_deferred"),
                       ("Frontend file cycles", "frontend_files")):
        if cyc[key]:
            add(f"**{title}:**")
            add("")
            for c in cyc[key][:8]:
                add(f"- {' <-> '.join(f'`{n}`' for n in c[:8])}{' ...' if len(c) > 8 else ''} ({len(c)} nodes)")
            add("")

    # Dead code
    add("## 5. Dead code and unused dependencies")
    add("")
    dead = data["dead_code"]
    py_unused = [d for d in dead["python"] if d["status"] == "unused"]
    py_test_only = [d for d in dead["python"] if d["status"] == "test-only"]
    ts_unused = [d for d in dead["typescript"] if d["status"] == "unused"]
    ts_test_only = [d for d in dead["typescript"] if d["status"] == "test-only"]
    ts_unneeded = [d for d in dead["typescript"] if d["status"] == "export-unneeded"]
    add(f"- Python: **{len(py_unused)}** definitions never referenced; **{len(py_test_only)}** referenced only by tests.")
    add(f"- TypeScript: **{len(ts_unused)}** exports never imported; **{len(ts_test_only)}** imported only by tests; "
        f"**{len(ts_unneeded)}** exported but only used inside their own file (drop the `export`); "
        f"**{len(dead['unreachable_files'])}** files unreachable from `main.tsx`.")
    add("")
    add("Name-based detection: confirm before deleting (a dynamic `getattr`, a string route, or an external "
        "caller can keep a symbol alive).")
    add("")
    if py_unused or ts_unused:
        add(_table(["Symbol", "Kind", "Where"],
                   [[f"`{d['name']}`", d["kind"], f"`{d['file']}:{d['line']}`"] for d in (py_unused + ts_unused)[:30]]))
        add("")
    if dead["unreachable_files"]:
        add("Unreachable frontend files: " + ", ".join(f"`{f}`" for f in dead["unreachable_files"]))
        add("")
    if data["unused_dependencies"]:
        add(_table(["Manifest", "Package", "Why flagged"],
                   [[d["manifest"], f"`{d['package']}`", d["note"]] for d in data["unused_dependencies"]]))
    else:
        add("No unused dependencies.")
    add("")

    # Growth
    add("## 6. Code growth vs feature growth")
    add("")
    g = data["growth"]
    if g:
        add("Production lines per API endpoint and per implemented user story, at every commit. A rising line "
            "means each new feature costs more code than the last (accidental complexity); flat or falling means "
            "the codebase is absorbing features through reuse.")
        add("")
        first, last = g[0], g[-1]
        def ratio(p, k):
            return round(p["prod_loc"] / p[k]) if p[k] else 0
        add(_table(
            ["", "First commit", "Latest commit", "Change"],
            [
                ["Production lines", f"{first['prod_loc']:,}", f"{last['prod_loc']:,}", f"x{last['prod_loc'] / max(first['prod_loc'], 1):.1f}"],
                ["Test lines", f"{first['test_loc']:,}", f"{last['test_loc']:,}", f"x{last['test_loc'] / max(first['test_loc'], 1):.1f}"],
                ["API endpoints", first["endpoints"], last["endpoints"], f"x{last['endpoints'] / max(first['endpoints'], 1):.1f}"],
                ["User stories implemented", first["stories_implemented"], last["stories_implemented"],
                 f"x{last['stories_implemented'] / max(first['stories_implemented'], 1):.1f}"],
                ["Lines per endpoint", ratio(first, "endpoints"), ratio(last, "endpoints"), ""],
                ["Lines per implemented story", ratio(first, "stories_implemented"), ratio(last, "stories_implemented"), ""],
                ["Test lines per production line", round(first["test_loc"] / max(first["prod_loc"], 1), 2),
                 round(last["test_loc"] / max(last["prod_loc"], 1), 2), ""],
            ],
        ))
        add("")
        step = max(1, len(g) // 20)
        sample = g[::step] + ([g[-1]] if g[-1] not in g[::step] else [])
        labels = ", ".join(f'"{p["date"][5:]}"' for p in sample)
        add("```mermaid")
        add("xychart-beta")
        add('    title "Production lines per implemented story (lower is better)"')
        add(f"    x-axis [{labels}]")
        add('    y-axis "Lines per story"')
        add(f"    line [{', '.join(str(ratio(p, 'stories_implemented')) for p in sample)}]")
        add("```")
        add("")
        add("```mermaid")
        add("xychart-beta")
        add('    title "Production lines (bars) vs implemented stories x100 (line)"')
        add(f"    x-axis [{labels}]")
        add(f"    bar [{', '.join(str(p['prod_loc']) for p in sample)}]")
        add(f"    line [{', '.join(str(p['stories_implemented'] * 100) for p in sample)}]")
        add("```")
        add("")
    else:
        add("_History not analysed._")
        add("")

    # Mutation
    add("## 7. Mutation score")
    add("")
    add("Share of deliberately injected bugs (flipped comparison, and/or swap, off-by-one, ...) that the tests "
        "catch. Coverage says a line ran; mutation score says the tests would notice if it were wrong. Run "
        "`python -m tools.code_health mutation --module <path>` to measure a module.")
    add("")
    if data["mutation"]:
        add(_table(["Module", "Score", "Killed", "Survived", "Tested / sites", "Measured", "Tests"],
                   [[f"`{m['module']}`", f"{m['score']:.0%}" if m["score"] is not None else "-", m["killed"], m["survived"],
                     f"{m['tested']} / {m['sites']}", m["measured_at"], ", ".join(f"`{t}`" for t in m["tests"])]
                    for m in sorted(data["mutation"].values(), key=lambda m: m["score"] or 0)]))
        add("")
        survivors = [(m["module"], s) for m in data["mutation"].values() for s in m["survivors"]]
        if survivors:
            add("Surviving mutants (each is a test that's missing):")
            add("")
            add(_table(["Module", "Line", "Mutation"],
                       [[f"`{mod}`", s["line"], s["description"]] for mod, s in survivors[:25]]))
            add("")
    else:
        add("_Not measured yet._")
        add("")
    return "\n".join(lines) + "\n"
