# Code health measures

`python -m tools.code_health` measures the backend (Python) and frontend
(TypeScript/React) and writes `docs/code-health/report.md` and
`report.json`. It has no dependencies beyond the backend's own and Node,
and uses the frontend's `typescript` package to parse TS/TSX.

```sh
python -m tools.code_health report              # measure everything (about 5 s)
python -m tools.code_health report --no-history # skip git-history measures
python -m tools.code_health check               # exit 1 if anything got worse than the baseline
python -m tools.code_health baseline            # accept today's numbers as the new baseline
python -m tools.code_health mutation --module app/oracles/rules.py --tests tests/test_oracles.py --max 40
```

Mutation testing runs the backend tests, so it needs the test database
(`DATABASE_URL` pointing at a migrated test database, as for `pytest`).

## What is measured, and how

| Measure | How | Why it matters |
|---|---|---|
| **Cyclomatic complexity** | McCabe: 1 + each if/elif, loop, except, ternary, case, comprehension clause, extra boolean operand. Per function (`pyanalyze.py`, `ts_analyze.mjs`). Limit 10 | The minimum number of tests that cover every branch |
| **Cognitive complexity** | SonarSource's definition: +1 per break in linear flow, plus nesting depth for nested ones, +1 per run of like boolean operators. Limit 15 | How hard a function is to *read*; predicts defects better than cyclomatic |
| **Duplication** | Token-based clones of 70+ tokens and 6+ lines, ignoring whitespace and comments (*exact*, like jscpd/CPD), and again with identifiers and literals normalised (*structural*, 100+ tokens and 10+ lines, for copy-then-rename) (`duplication.py`) | Each copy is a place a fix can be missed |
| **Hotspots** | Commits touching a file x the sum of its functions' cognitive complexity, over git history (`history.py`) | Complexity that's also changing often is where defects and slow changes concentrate (Tornhill) |
| **Coupling** | Internal import graph; per package and module: afferent (Ca), efferent (Ce), instability Ce/(Ca+Ce) (`graph.py`) | Highly coupled modules make every change wide |
| **Dependency cycles** | Tarjan strongly-connected components, with and without function-level ("lazy") imports | A cycle hidden behind lazy imports still prevents isolating or extracting a package |
| **Dead code** | Python: definitions never referenced by name, attribute, import or string (vulture-style), skipping framework entry points; *test-only* reported separately. TS: exports nobody imports, exports only used in their own file, files unreachable from `main.tsx` (`deadcode.py`) | Dead code costs reading time and misleads |
| **Unused dependencies** | Declared packages (`pyproject.toml`, `package.json`) with no import, script or config use, after a name map and an allow-list of implicitly used packages | Smaller attack surface and faster builds |
| **Code growth vs feature growth** | At every commit: production/test lines vs API endpoints and implemented user stories (`USn.m --` lines in implemented epics) | Lines per feature rising means accidental complexity is growing |
| **Mutation score** | AST mutants (flipped comparison, and/or, +/-, True/False, off-by-one, dropped `not`, `return None`), sampled and seeded, applied to a temp copy of `backend/`; the module's tests run against each (`mutation.py`) | Whether the tests would *notice* a bug, which coverage can't tell |

## The ratchet

`check` compares today's numbers with `docs/code-health/baseline.json`
and fails if:

- functions over the complexity limits, import cycles, unused definitions
  or unused dependencies rose
- duplication rose by more than 0.3 points or passed 5%
- a module's mutation score fell by more than 5 points

Thresholds live in `thresholds.json`. Improve something, then run
`baseline` to lock the gain in.

## Known limits

- Dead-code detection is name-based: two things sharing a name keep each
  other alive, and anything called dynamically by string is treated as
  used. Confirm before deleting.
- Some mutants are equivalent: changing an enum-like constant from 0 to 1
  can't be caught. Read survivors, don't chase 100%.
- Hotspot churn counts commits, so a few large commits weigh like many
  small ones.
