# Code health

Measured by `python -m tools.code_health` (see
[tools/code_health/README.md](../../tools/code_health/README.md)): cognitive
and cyclomatic complexity, duplication (exact and renamed copies), hotspots
(churn x complexity), coupling and dependency cycles, dead code and unused
dependencies, code growth against feature growth, and mutation score.
`python -m tools.code_health check` fails when any of these gets worse than
the committed baseline (`docs/code-health/baseline.json`).

<!-- BEGIN GENERATED: code-health-summary -->
| Measure | Value |
|---|---|
| Production lines (backend / frontend) | 20,996 / 14,036 |
| Functions over cognitive complexity 15 | 78 / 43 |
| Most complex function | `run_orchestration` (98) |
| Duplication, exact / structural (backend) | 0.25% / 4.13% |
| Import cycles (import-time / incl. lazy imports) | 0 / 2 |
| Top hotspot | `backend/app/db/repository.py` |
| Mutation score (modules measured) | `rules.py` 68%, `gateway.py` 84%, `results.py` 72%, `orchestrator.py` 56%, `efficiency.py` 76%, `records.py` 88% |

Full report: [docs/code-health/report.md](../code-health/report.md).
<!-- END GENERATED: code-health-summary -->

## Reading the numbers

- **Complexity is concentrated, not spread.** The averages are healthy
  (about 5 per function), but a handful of functions carry most of it.
  These are the run loops (`run_scenario`, `run_orchestration`), the batch
  results builder, and the large React panels (`BlueprintPage`,
  `DigitalTwinAdvancedPanel`). They are also where changes keep landing,
  so they top the hotspot list.
- **Duplication is low as copy-paste, higher as copy-and-rename.** The
  structural clones are route handlers repeated across `api/twin.py`,
  `api/orchestration.py` and `api/evaluation.py`, and repeated
  load-and-convert blocks in `db/repository.py`.
- **There are no import-time cycles, but cycles have been dodged.**
  Dozens of function-level imports exist to break cycles. Counted, they
  form one package cycle spanning most of the backend: jobs -> main ->
  every router -> services -> jobs.
- **Mutation scores are 56-88%** on the six modules measured. Each surviving
  mutant is a missing assertion, listed in the full report. The new Epic 36
  modules first scored 40% (`efficiency.py`) and 64% (`records.py`) with
  all their tests passing. Tests written against the survivors raised them
  to 76% and 88%: passing tests aren't the same as tests that would notice
  a bug.
