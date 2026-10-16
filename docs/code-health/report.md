# Code health report

_Generated 2026-10-16 by `python -m tools.code_health report`. Do not edit by hand: rerun the command (or `python -m tools.docs sync`) to refresh it. How each measure is computed is described in [tools/code_health/README.md](../../tools/code_health/README.md)._

## Scorecard

| Measure | Backend (Python) | Frontend (TS/React) | Target | Status |
|---|---|---|---|---|
| Production code (non-blank lines) | 26,821 | 16,385 | - |  |
| Test code (non-blank lines) | 10,904 | 3,416 | - |  |
| Functions measured | 1273 | 625 | - |  |
| Mean cognitive complexity / function | 4.45 | 4.8 | <= 6 | OK |
| Functions over cognitive 15 | 86 | 50 | ratchet: must not rise |  |
| Functions over cyclomatic 10 | 126 | 69 | ratchet: must not rise |  |
| Duplicated lines | 0.28% | 0.09% | <= 5.0% | OK |
| Structural duplication (renamed copies) | 3.39% | 0.63% | ratchet |  |
| Import cycles (module level, import-time) | 0 | 0 | 0 | OK |
| Package cycles (incl. lazy imports) | 1 | 1 | ratchet |  |
| Unused definitions / exports | 14 | 11 | ratchet |  |
| Unused dependencies | 2 | 0 | 0 | **Attention** |
| Mutation score (mean of measured modules) | 0.74 | - | >= 0.7 | OK |

## 1. Complexity

Cognitive complexity measures how hard a function is to *read* (nesting is penalised); cyclomatic complexity counts independent paths, i.e. the minimum number of tests to cover every branch. Limits: cognitive 15, cyclomatic 10.

### Most complex functions

| Function | File | Cognitive | Cyclomatic | Lines |
|---|---|---|---|---|
| `run_orchestration` | `backend/app/twin/orchestrator.py:236` | 98 | 55 | 517 |
| `DigitalTwinAdvancedPanel` | `frontend/src/components/DigitalTwinAdvancedPanel.tsx:48` | 93 | 51 | 466 |
| `BlueprintPage` | `frontend/src/pages/BlueprintPage.tsx:34` | 91 | 71 | 421 |
| `run_scenario` | `backend/app/twin/engine.py:305` | 83 | 63 | 350 |
| `batch_results` | `backend/app/batches/results.py:201` | 78 | 67 | 111 |
| `run_scenario.process_calls` | `backend/app/twin/engine.py:407` | 66 | 21 | 104 |
| `OrchestrationPanel` | `frontend/src/components/OrchestrationPanel.tsx:53` | 65 | 37 | 433 |
| `merge_process_schemas` | `backend/app/ingestion/merge.py:37` | 62 | 24 | 80 |
| `patch_group` | `backend/app/scim/service.py:354` | 60 | 35 | 48 |
| `run_orchestration.run_task_node` | `backend/app/twin/orchestrator.py:371` | 60 | 35 | 140 |
| `_sanitize_situation` | `backend/app/twin/test_builder.py:264` | 57 | 52 | 72 |
| `_apply_operation` | `backend/app/bpmn/chat_ops.py:81` | 53 | 36 | 71 |
| `BatchResultsPage` | `frontend/src/pages/BatchResultsPage.tsx:41` | 53 | 44 | 243 |
| `compliance_evidence` | `backend/app/context/compliance.py:89` | 52 | 37 | 94 |
| `BatchPanel` | `frontend/src/components/datasets/BatchPanel.tsx:32` | 52 | 33 | 215 |

### Longest functions

| Function | File | Lines | Cognitive |
|---|---|---|---|
| `TwinTestBuilder` | `frontend/src/components/TwinTestBuilder.tsx:85` | 549 | 51 |
| `run_orchestration` | `backend/app/twin/orchestrator.py:236` | 517 | 98 |
| `DigitalTwinAdvancedPanel` | `frontend/src/components/DigitalTwinAdvancedPanel.tsx:48` | 466 | 93 |
| `OrchestrationPanel` | `frontend/src/components/OrchestrationPanel.tsx:53` | 433 | 65 |
| `BlueprintPage` | `frontend/src/pages/BlueprintPage.tsx:34` | 421 | 91 |
| `run_scenario` | `backend/app/twin/engine.py:305` | 350 | 83 |
| `DiagramPage` | `frontend/src/pages/DiagramPage.tsx:11` | 280 | 44 |
| `TestDataKitchen` | `frontend/src/components/TestDataKitchen.tsx:32` | 266 | 38 |
| `ProcessDetailPage` | `frontend/src/pages/ProcessDetailPage.tsx:49` | 266 | 48 |
| `OraclePanel` | `frontend/src/components/datasets/OraclePanel.tsx:28` | 265 | 38 |

## 2. Duplication

Copy-paste clones of at least 70 tokens and 6 lines, ignoring whitespace and comments, in production code.

**Backend:** 74 of 26,821 lines (0.28%) sit in 3 exact clone pairs; 3.39% sit in 36 structural clones (same shape with identifiers and literals renamed, at least 100 tokens and 10 lines).

| Tokens | Lines | First copy | Second copy |
|---|---|---|---|
| 90 | 12 | `backend/app/twin/engine.py:516-527` | `backend/app/twin/engine.py:566-577` |
| 89 | 12 | `backend/app/api/oidc.py:126-137` | `backend/app/api/operator.py:191-200` |
| 85 | 14 | `backend/app/db/repository.py:1378-1391` | `backend/app/db/repository.py:1965-1978` |

Largest structural clones (backend):

| Tokens | Lines | First copy | Second copy |
|---|---|---|---|
| 283 | 34 | `backend/app/api/orchestration.py:45-78` | `backend/app/api/twin.py:57-90` |
| 242 | 34 | `backend/app/api/evaluation.py:11-44` | `backend/app/api/orchestration.py:12-43` |
| 185 | 33 | `backend/app/db/repository.py:1693-1724` | `backend/app/db/repository.py:2088-2120` |
| 183 | 26 | `backend/app/main.py:169-193` | `backend/app/main.py:183-208` |
| 149 | 27 | `backend/app/db/repository.py:942-968` | `backend/app/db/repository.py:1693-1718` |
| 149 | 27 | `backend/app/db/repository.py:942-968` | `backend/app/db/repository.py:2088-2113` |
| 143 | 17 | `backend/app/db/models.py:1010-1023` | `backend/app/db/models.py:1122-1138` |
| 132 | 29 | `backend/app/db/repository.py:1304-1330` | `backend/app/db/repository.py:1888-1916` |

**Frontend:** 14 of 16,385 lines (0.09%) sit in 1 exact clone pairs; 0.63% sit in 2 structural clones (same shape with identifiers and literals renamed, at least 100 tokens and 10 lines).

| Tokens | Lines | First copy | Second copy |
|---|---|---|---|
| 81 | 7 | `frontend/src/components/org/ApplicableContextView.tsx:48-54` | `frontend/src/components/org/PolicyReviewPanel.tsx:145-151` |

Largest structural clones (frontend):

| Tokens | Lines | First copy | Second copy |
|---|---|---|---|
| 156 | 11 | `frontend/src/components/org/ApplicableContextView.tsx:46-56` | `frontend/src/components/ProcessScopeEditor.tsx:78-88` |
| 110 | 43 | `frontend/src/components/DigitalTwinPreview.tsx:26-64` | `frontend/src/components/DigitalTwinPreviewOrchestrated.tsx:32-74` |

## 3. Hotspots (churn x complexity)

Files that are both complicated and frequently changed. Score = commits touching the file x the sum of its functions' cognitive complexity. Refactoring the top of this list pays back fastest.

| File | Commits | Lines changed | Cognitive (sum) | Lines | Score |
|---|---|---|---|---|---|
| `backend/app/db/repository.py` | 23 | 2,864 | 337 | 2168 | 7,751 |
| `backend/app/twin/engine.py` | 9 | 1,054 | 219 | 596 | 1,971 |
| `frontend/src/api/client.ts` | 26 | 1,575 | 67 | 1144 | 1,742 |
| `frontend/src/pages/BlueprintPage.tsx` | 12 | 542 | 106 | 420 | 1,272 |
| `backend/app/twin/orchestrator.py` | 5 | 1,304 | 246 | 685 | 1,230 |
| `backend/app/oracles/service.py` | 4 | 755 | 241 | 582 | 964 |
| `frontend/src/pages/ProcessDetailPage.tsx` | 10 | 370 | 74 | 287 | 740 |
| `frontend/src/pages/DiagramPage.tsx` | 8 | 326 | 68 | 261 | 544 |
| `frontend/src/components/OrchestrationPanel.tsx` | 5 | 525 | 103 | 461 | 515 |
| `backend/app/batches/results.py` | 2 | 519 | 201 | 434 | 402 |
| `backend/app/twin/test_builder.py` | 2 | 564 | 177 | 491 | 354 |
| `backend/app/context/ingestion.py` | 2 | 662 | 175 | 552 | 350 |
| `frontend/src/components/TwinTestBuilder.tsx` | 3 | 637 | 115 | 593 | 345 |
| `backend/app/config.py` | 12 | 316 | 28 | 267 | 336 |
| `backend/app/scim/service.py` | 2 | 443 | 158 | 335 | 316 |

## 4. Coupling and dependency cycles

Ca = how many internal packages depend on this one; Ce = how many it depends on; instability I = Ce/(Ca+Ce). Packages near I=0 should be stable foundations; near I=1 should be leaves.

### Backend packages

| Package | Ca (in) | Ce (out) | Instability |
|---|---|---|---|
| `app.db` | 20 | 16 | 0.44 |
| `app.api` | 1 | 26 | 0.96 |
| `app.context` | 10 | 10 | 0.5 |
| `app.llm` | 14 | 6 | 0.3 |
| `app.ids` | 19 | 0 | 0.0 |
| `app.schemas` | 18 | 1 | 0.05 |
| `app.config` | 16 | 0 | 0.0 |
| `app.jobs` | 7 | 9 | 0.56 |
| `app.batches` | 5 | 10 | 0.67 |
| `app.oracles` | 5 | 10 | 0.67 |
| `app.connectors` | 5 | 9 | 0.64 |
| `app.twin` | 4 | 9 | 0.69 |
| `app.process_twin` | 2 | 10 | 0.83 |
| `app.main` | 1 | 10 | 0.91 |
| `app.tenancy` | 8 | 3 | 0.27 |
| `app.bpmn` | 7 | 3 | 0.3 |
| `app.datasets` | 6 | 4 | 0.4 |
| `app.ingestion` | 3 | 7 | 0.7 |
| `app.auth` | 4 | 5 | 0.56 |
| `app.catalog` | 3 | 6 | 0.67 |
| `app.store` | 9 | 0 | 0.0 |
| `app.operators` | 1 | 6 | 0.86 |
| `app.gap_analysis` | 2 | 4 | 0.67 |
| `app.scim` | 2 | 4 | 0.67 |
| `app.registry` | 2 | 3 | 0.6 |
| `app.agents` | 2 | 2 | 0.5 |
| `app.blueprint` | 1 | 3 | 0.75 |
| `app.chat` | 1 | 3 | 0.75 |
| `app.request_context` | 1 | 0 | 0.0 |

### Most-coupled backend modules

| Module | Ca (in) | Ce (out) | Instability |
|---|---|---|---|
| `app.db.repository` | 29 | 44 | 0.6 |
| `app.db.models` | 57 | 0 | 0.0 |
| `app.main` | 1 | 39 | 0.97 |
| `app.api.deps` | 29 | 10 | 0.26 |
| `app.ids` | 39 | 0 | 0.0 |
| `app.config` | 35 | 0 | 0.0 |
| `app.context.repository` | 15 | 6 | 0.29 |
| `app.jobs.worker` | 8 | 12 | 0.6 |
| `app.llm` | 16 | 4 | 0.2 |
| `app.schemas.context` | 20 | 0 | 0.0 |

### Frontend directories

| Directory | Ca (in) | Ce (out) | Instability |
|---|---|---|---|
| `frontend/brandassets` | 1 | 0 | 0.0 |
| `frontend/src` | 0 | 5 | 1.0 |
| `frontend/src/api` | 8 | 0 | 0.0 |
| `frontend/src/auth` | 4 | 3 | 0.43 |
| `frontend/src/components` | 4 | 5 | 0.56 |
| `frontend/src/components/datasets` | 2 | 1 | 0.33 |
| `frontend/src/components/org` | 2 | 2 | 0.5 |
| `frontend/src/lib` | 3 | 1 | 0.25 |
| `frontend/src/operator` | 0 | 3 | 1.0 |
| `frontend/src/pages` | 1 | 7 | 0.88 |
| `frontend/src/styles` | 2 | 0 | 0.0 |

### Cycles

- Backend module cycles at import time: **0**
- Backend module cycles including function-level (lazy) imports: **2** (54 lazy import edges in total -- usually a sign a cycle was dodged rather than removed)
- Backend package cycles including lazy imports: **1**
- Frontend file cycles: **0**, directory cycles: **1**

**Backend package cycles (incl. lazy imports):**

- `app.agents` <-> `app.api` <-> `app.auth` <-> `app.batches` <-> `app.blueprint` <-> `app.bpmn` <-> `app.catalog` <-> `app.chat` ... (25 nodes)

**Backend module cycles (incl. lazy imports):**

- `app.api.batches` <-> `app.api.connectors` <-> `app.api.process_twin` <-> `app.connectors.jobs` <-> `app.context.jobs` <-> `app.jobs.worker` <-> `app.main` <-> `app.oracles.jobs` ... (9 nodes)
- `app.context.consumers` <-> `app.db.repository` (2 nodes)

## 5. Dead code and unused dependencies

- Python: **14** definitions never referenced; **5** referenced only by tests.
- TypeScript: **11** exports never imported; **3** imported only by tests; **81** exported but only used inside their own file (drop the `export`); **0** files unreachable from `main.tsx`.

Name-based detection: confirm before deleting (a dynamic `getattr`, a string route, or an external caller can keep a symbol alive).

| Symbol | Kind | Where |
|---|---|---|
| `bpmn_id_for_lane` | function | `backend/app/bpmn/chat_ops.py:45` |
| `citations_from_labels` | function | `backend/app/context/consumers.py:178` |
| `delete_policy_file` | function | `backend/app/context/ingestion.py:85` |
| `get_entity` | function | `backend/app/context/repository.py:174` |
| `get_entity_version_snapshot` | function | `backend/app/context/repository.py:328` |
| `list_context_documents` | function | `backend/app/context/repository.py:438` |
| `cited_entities` | function | `backend/app/context/resolver.py:399` |
| `list_schema_changes` | function | `backend/app/db/repository.py:331` |
| `_set_transaction_tenant` | function | `backend/app/db/session.py:47` |
| `registered_kinds` | function | `backend/app/jobs/worker.py:61` |
| `grand_total_cost_usd` | method | `backend/app/llm/usage.py:144` |
| `get_registry` | function | `backend/app/registry/manager.py:45` |
| `Timestamped` | class | `backend/app/schemas/common.py:55` |
| `VersionDiffRequest` | class | `backend/app/schemas/versions.py:30` |
| `updateAgentGovernance` | export | `frontend/src/api/client.ts:499` |
| `pushToRegistry` | export | `frontend/src/api/client.ts:633` |
| `cancelTwinRun` | export | `frontend/src/api/client.ts:735` |
| `resumeTwinRun` | export | `frontend/src/api/client.ts:739` |
| `updateOrganization` | export | `frontend/src/api/client.ts:904` |
| `listPolicyPassages` | export | `frontend/src/api/client.ts:976` |
| `getProcessScope` | export | `frontend/src/api/client.ts:989` |
| `previewContextPack` | export | `frontend/src/api/client.ts:1002` |
| `getDataset` | export | `frontend/src/api/client.ts:1129` |
| `addDatasetRows` | export | `frontend/src/api/client.ts:1143` |
| `DocumentDetail` | export | `frontend/src/api/types.ts:72` |

| Manifest | Package | Why flagged |
|---|---|---|
| pyproject.toml [dependencies] | `pyjwt` | no `import pyjwt` found |
| pyproject.toml [optional-dependencies.dev] | `markdown` | no `import markdown` found |

## 6. Code growth vs feature growth

Production lines per API endpoint and per implemented user story, at every commit. A rising line means each new feature costs more code than the last (accidental complexity); flat or falling means the codebase is absorbing features through reuse.

|  | First commit | Latest commit | Change |
|---|---|---|---|
| Production lines | 2,985 | 42,468 | x14.2 |
| Test lines | 1,577 | 13,998 | x8.9 |
| API endpoints | 22 | 234 | x10.6 |
| User stories implemented | 21 | 241 | x11.5 |
| Lines per endpoint | 136 | 181 |  |
| Lines per implemented story | 142 | 176 |  |
| Test lines per production line | 0.53 | 0.33 |  |

```mermaid
xychart-beta
    title "Production lines per implemented story (lower is better)"
    x-axis ["09-18", "09-18", "09-19", "09-19", "09-19", "09-19", "09-19", "09-20", "09-20", "09-20", "09-21", "09-22", "09-24", "09-24", "09-29", "09-29", "09-30", "10-11", "10-11", "10-12", "10-13", "10-14", "10-14", "10-15", "10-15", "10-15", "10-15", "10-16", "10-16"]
    y-axis "Lines per story"
    line [142, 165, 244, 272, 246, 267, 267, 172, 158, 161, 160, 167, 171, 175, 166, 162, 157, 154, 154, 164, 164, 164, 164, 166, 166, 176, 167, 170, 176]
```

```mermaid
xychart-beta
    title "Production lines (bars) vs implemented stories x100 (line)"
    x-axis ["09-18", "09-18", "09-19", "09-19", "09-19", "09-19", "09-19", "09-20", "09-20", "09-20", "09-21", "09-22", "09-24", "09-24", "09-29", "09-29", "09-30", "10-11", "10-11", "10-12", "10-13", "10-14", "10-14", "10-15", "10-15", "10-15", "10-15", "10-16", "10-16"]
    bar [2985, 3456, 5131, 5707, 6404, 6941, 6941, 7041, 7438, 8526, 10393, 10861, 11101, 11400, 14081, 16076, 16688, 19104, 19104, 32987, 32993, 37042, 37042, 37618, 37618, 39839, 40213, 40983, 42468]
    line [2100, 2100, 2100, 2100, 2600, 2600, 2600, 4100, 4700, 5300, 6500, 6500, 6500, 6500, 8500, 9900, 10600, 12400, 12400, 20100, 20100, 22600, 22600, 22600, 22600, 22600, 24100, 24100, 24100]
```

## 7. Mutation score

Share of deliberately injected bugs (flipped comparison, and/or swap, off-by-one, ...) that the tests catch. Coverage says a line ran; mutation score says the tests would notice if it were wrong. Run `python -m tools.code_health mutation --module <path>` to measure a module.

| Module | Score | Killed | Survived | Tested / sites | Measured | Tests |
|---|---|---|---|---|---|---|
| `app/twin/orchestrator.py` | 56% | 14 | 11 | 25 / 172 | 2026-10-13 | `tests/test_orchestrator.py` |
| `app/oracles/rules.py` | 68% | 17 | 8 | 25 / 116 | 2026-10-13 | `tests/test_oracles.py` |
| `app/batches/results.py` | 72% | 18 | 7 | 25 / 239 | 2026-10-13 | `tests/test_results.py` |
| `app/process_twin/efficiency.py` | 76% | 19 | 6 | 25 / 167 | 2026-10-13 | `tests/test_process_twin.py` |
| `app/twin/gateway.py` | 84% | 21 | 4 | 25 / 39 | 2026-10-13 | `tests/test_twin_gateway.py` |
| `app/twin/records.py` | 88% | 22 | 3 | 25 / 74 | 2026-10-13 | `tests/test_process_twin.py` |

Surviving mutants (each is a test that's missing):

| Module | Line | Mutation |
|---|---|---|
| `app/oracles/rules.py` | 31 | number 0 -> 1 |
| `app/oracles/rules.py` | 31 | number 2 -> 3 |
| `app/oracles/rules.py` | 38 | number 0 -> 1 |
| `app/oracles/rules.py` | 110 | return value -> None |
| `app/oracles/rules.py` | 125 | return value -> None |
| `app/oracles/rules.py` | 137 | constant False -> True |
| `app/oracles/rules.py` | 174 | constant False -> True |
| `app/oracles/rules.py` | 51 | arithmetic Add -> Sub |
| `app/twin/gateway.py` | 102 | number 0 -> 1 |
| `app/twin/gateway.py` | 118 | comparison Gt -> GtE |
| `app/twin/gateway.py` | 146 | comparison Gt -> GtE |
| `app/twin/gateway.py` | 160 | arithmetic Sub -> Add |
| `app/batches/results.py` | 63 | number 0 -> 1 |
| `app/batches/results.py` | 247 | boolean or -> and |
| `app/batches/results.py` | 51 | number 0 -> 1 |
| `app/batches/results.py` | 56 | number 4 -> 5 |
| `app/batches/results.py` | 247 | comparison Is -> IsNot |
| `app/batches/results.py` | 366 | number 4 -> 5 |
| `app/batches/results.py` | 503 | comparison Eq -> NotEq |
| `app/twin/orchestrator.py` | 118 | number 0 -> 1 |
| `app/twin/orchestrator.py` | 158 | return value -> None |
| `app/twin/orchestrator.py` | 223 | number 50 -> 51 |
| `app/twin/orchestrator.py` | 251 | return value -> None |
| `app/twin/orchestrator.py` | 261 | return value -> None |
| `app/twin/orchestrator.py` | 65 | boolean or -> and |

