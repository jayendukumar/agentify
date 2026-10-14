# Keeping these docs current

The wiki is designed so facts can't silently go stale:

- **Facts are generated from the code.** Tables, columns and
  relationships come from the SQLAlchemy models. Endpoints come from the
  OpenAPI document. The prompt catalogue comes from every
  `LLMClient.complete` call site. Package dependencies come from the
  import graph, the epic index from `planning/epics`, and the code-health
  summary from `tools/code_health`. They sit between
  `<!-- BEGIN GENERATED: name -->` / `<!-- END GENERATED: name -->` markers.
  Don't edit inside the markers.
- **Judgement is written by people,** and dated. Each narrative page
  carries `Last reviewed: <date> · Sources: <paths>`. The tooling reports a
  page for review when any of its sources changed after that date.

## Commands

Run from the repository root (Python 3.11+, with the backend's
dependencies installed, and Node for the frontend analysis):

| Command | What it does |
|---|---|
| `python -m tools.docs sync` | Regenerate every generated block (the "update docs now" button) |
| `python -m tools.docs sync --with-health` | Re-measure code health first, then regenerate |
| `python -m tools.docs check` | Exit 1 if a block is stale, a link is broken, or a table, epic or LLM operation isn't documented; list narrative pages due for review |
| `python -m tools.docs status` | Show each page's blocks, whether each is current, and its review date |
| `python -m tools.code_health report` | Re-measure code health (`docs/code-health/report.md`) |
| `python -m tools.code_health check` | Exit 1 if code health got worse than the baseline |

## Making it automatic

- **Before every commit:** `tools/docs/pre-commit` runs both checks. Install
  it with `git config core.hooksPath tools/hooks` (it lives at
  `tools/hooks/pre-commit`).
- **On demand with Claude Code:** the `update-docs` skill
  (`.claude/skills/update-docs/`) runs `sync --with-health`, then reviews
  every page `check` lists for review against its changed sources, and
  updates the prose and the "Last reviewed" date.
- **In CI**, when there is one: run `python -m tools.docs check` and
  `python -m tools.code_health check` as required steps.

## When you add something

| You added | Then |
|---|---|
| A table | Add it to a domain in `tools/docs/generators.py` `DOMAINS` (`check` fails until you do) |
| An LLM call | Give its `operation` a purpose in `_PURPOSE` (same file) |
| An epic | Link it from `planning/00-index.md` |
| A wiki page | Link it from [Home](Home.md); add `Last reviewed` / `Sources` if it's narrative |
