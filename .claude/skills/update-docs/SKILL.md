---
name: update-docs
description: Use when the user asks to update, refresh, or sync the documentation/wiki, or after a feature lands that changes architecture, data model, prompts, API, or the critical review in the Agentic Solution Generator (Axyntro) project. Regenerates the generated wiki blocks and code-health report, then reviews hand-written pages whose sources changed.
---

# Update the wiki

The wiki lives in `docs/wiki/`. Facts are generated between
`<!-- BEGIN GENERATED: x -->` markers; judgement is hand-written and dated
(`Last reviewed: YYYY-MM-DD · Sources: ...`). See
`docs/wiki/Keeping-Docs-Current.md`.

1. Run `python -m tools.docs sync --with-health` from the repo root.
2. Run `python -m tools.docs check`. Fix every FAIL:
   - unmapped table: add it to `DOMAINS` in `tools/docs/generators.py`
   - LLM operation without a purpose: add it to `_PURPOSE` in the same file
   - epic not indexed: link it from `planning/00-index.md`
   - broken link: fix the link
3. For every REVIEW line, read the page and `git log -p --since=<last reviewed> -- <sources>`;
   update the prose where the change makes it wrong or incomplete (new
   components in Architecture diagrams, new weaknesses or fixed ones in
   Critical-Review, new lifecycles in Data-Model), then set "Last reviewed"
   to today. Don't edit inside generated markers.
4. Re-run `python -m tools.docs check` until it passes, and summarise what changed.
