# Docs tooling

Keeps `docs/wiki` true to the code. Full guide:
[docs/wiki/Keeping-Docs-Current.md](../../docs/wiki/Keeping-Docs-Current.md).

```sh
python -m tools.docs sync [--with-health]   # regenerate generated blocks (and code health)
python -m tools.docs check                  # fail on stale blocks, broken links, undocumented tables/epics/LLM calls
python -m tools.docs status                 # per-page block status and review dates
git config core.hooksPath tools/hooks       # run both checks before every commit
```

Generators (`generators.py`) import the backend (models, FastAPI app) with a
placeholder API key, so no real secrets or database are needed. Every
generator is deterministic, with no timestamps, so `check` only fails when
the code really changed.
