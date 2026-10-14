# API reference

Generated from the OpenAPI document the API serves (`GET /openapi.json`;
interactive docs at `http://localhost:8000/docs`). Every route except
`/healthz` and login needs a session cookie from `POST /api/auth/login`.

<!-- BEGIN GENERATED: api-catalogue -->
**177 endpoints.** Access: *editor* needs the Editor role; *signed in* any role.

### agents

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/blueprint/agent-artifacts` | signed in |  |
| POST | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/approve` | editor |  |
| GET | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/compliance-evidence` | signed in | Epic 30, US30.7. |
| GET | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/compliance-evidence/export` | signed in |  |
| PATCH | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/governance` | editor | Epic 20, US20.1: editor-only, same tier as generate/publish (Epic 15's US15.6 precedent) -- widening an agent's declared permissions is at l |
| POST | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/mark-release-candidate` | editor | Epic 23, US23.4: editor-only, same tier as governance/publish. |
| POST | `/api/processes/{process_id}/blueprint/nodes/{node_id}/agent-artifact` | editor |  |

### auth

| Method | Path | Access | What it does |
|---|---|---|---|
| POST | `/api/auth/login` | public |  |
| POST | `/api/auth/logout` | signed in |  |
| GET | `/api/auth/me` | signed in |  |

### batches

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/batch-schedules` | signed in |  |
| POST | `/api/processes/{process_id}/batch-schedules` | editor |  |
| DELETE | `/api/processes/{process_id}/batch-schedules/{schedule_id}` | editor |  |
| GET | `/api/processes/{process_id}/batches` | signed in |  |
| GET | `/api/processes/{process_id}/batches/{batch_id}` | signed in |  |
| POST | `/api/processes/{process_id}/batches/{batch_id}/control` | editor | US34.7. |
| POST | `/api/processes/{process_id}/batches/{batch_id}/runs/{run_id}/replay` | editor | US34.6: the same row, trial, and seed -- on its own. |
| POST | `/api/processes/{process_id}/scenarios/{scenario_id}/batches` | editor |  |
| POST | `/api/processes/{process_id}/scenarios/{scenario_id}/batches/estimate` | signed in | US34.3: what a batch would cost and take, before starting it. |

### blueprint

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/blueprint` | signed in |  |
| GET | `/api/processes/{process_id}/blueprint/export` | signed in |  |
| POST | `/api/processes/{process_id}/blueprint/generate` | editor |  |
| PATCH | `/api/processes/{process_id}/blueprint/nodes/{node_id}` | editor |  |

### bpmn

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/bpmn` | signed in |  |
| PUT | `/api/processes/{process_id}/bpmn` | editor |  |
| POST | `/api/processes/{process_id}/bpmn/generate` | editor |  |

### catalog

| Method | Path | Access | What it does |
|---|---|---|---|
| POST | `/api/organization/applications/import` | editor | US29.2: preview first (`dry_run=true`, the default), then import. |
| GET | `/api/organization/applications/register` | signed in | US29.8. |
| PUT | `/api/organization/applications/{application_id}/api-spec` | signed in | US29.4: attach an OpenAPI document (JSON or YAML). |
| PUT | `/api/organization/applications/{application_id}/endpoints` | editor | US29.5: register endpoints. A production-tagged endpoint may be recorded for reference but is never called. Any other endpoint must pass the |
| GET | `/api/processes/{process_id}/systems` | signed in | US29.3: every system name in the process and what it resolves to. Exact matches are linked automatically on read. |
| PUT | `/api/processes/{process_id}/systems/{system_name}` | editor |  |

### chat

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/chat/messages` | signed in |  |
| POST | `/api/processes/{process_id}/chat/messages` | editor |  |
| POST | `/api/processes/{process_id}/chat/messages/{message_id}/apply` | editor |  |

### connectors

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/organization/connectors` | signed in |  |
| POST | `/api/organization/connectors` | editor |  |
| GET | `/api/organization/connectors/types` | signed in |  |
| DELETE | `/api/organization/connectors/{connector_id}` | editor | Deleting a connector keeps what it synced (as ordinary context with its connector source) -- removing context is a separate decision. |
| PUT | `/api/organization/connectors/{connector_id}` | editor |  |
| GET | `/api/organization/connectors/{connector_id}/runs` | signed in |  |
| POST | `/api/organization/connectors/{connector_id}/sync` | editor | Queued when the job worker runs; otherwise synced in this request. |

### datasets

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/datasets` | signed in |  |
| POST | `/api/processes/{process_id}/datasets` | editor |  |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}` | signed in |  |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/diff` | signed in | US32.6. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/holdout` | editor | US32.8. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/import` | editor | US32.3, step 2: import with the confirmed mapping. Columns marked hash/fake are pseudonymized locally before anything is stored. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/import/preview` | editor | US32.3, step 1: read the file and suggest a column mapping. Nothing is stored. |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/rows` | signed in | US32.8: holdout rows' labels are hidden unless `include_holdout` -- so they aren't looked at while the agent is being tuned. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/rows` | editor |  |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/rows/from-run` | editor | US32.7: a failed or surprising run becomes a golden case. |
| DELETE | `/api/processes/{process_id}/datasets/{dataset_id}/rows/{row_key}` | editor |  |
| PATCH | `/api/processes/{process_id}/datasets/{dataset_id}/rows/{row_key}` | editor | US32.4: correct or confirm a label (recorded with who did it). |
| PUT | `/api/processes/{process_id}/datasets/{dataset_id}/rows/{row_key}/ambiguity` | editor | US32.5. |

### documents

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/documents` | signed in |  |
| POST | `/api/processes/{process_id}/documents` | editor |  |
| DELETE | `/api/processes/{process_id}/documents/{document_id}` | editor |  |
| GET | `/api/processes/{process_id}/documents/{document_id}` | signed in |  |

### environments

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/twin-environments` | signed in |  |
| POST | `/api/processes/{process_id}/twin-environments` | editor |  |
| DELETE | `/api/processes/{process_id}/twin-environments/{environment_id}` | editor |  |
| PUT | `/api/processes/{process_id}/twin-environments/{environment_id}` | editor |  |

### evaluation

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/regression-suite-runs/{suite_run_id}` | signed in |  |
| GET | `/api/processes/{process_id}/regression-suites` | signed in |  |
| POST | `/api/processes/{process_id}/regression-suites` | editor |  |
| DELETE | `/api/processes/{process_id}/regression-suites/{suite_id}` | editor |  |
| POST | `/api/processes/{process_id}/regression-suites/{suite_id}/run` | editor | US19.3: same two-step create-then-enqueue handoff as app/api/twin.py's/app/api/orchestration.py's `start_run` -- one background task per sce |
| GET | `/api/processes/{process_id}/runs/compare` | signed in |  |
| GET | `/api/processes/{process_id}/runs/{run_id}/snapshot` | signed in | Epic 22, US22.1/US22.2/US22.3. |

### gap-analysis

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/gap-findings` | signed in |  |
| POST | `/api/processes/{process_id}/gap-findings/analyze` | editor |  |
| POST | `/api/processes/{process_id}/gap-findings/{finding_id}/dismiss` | editor |  |
| POST | `/api/processes/{process_id}/gap-findings/{finding_id}/resolve` | editor |  |

### health

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/healthz` | public |  |

### jobs

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/jobs` | signed in | US31.1: what the queue is doing. |

### oracles

| Method | Path | Access | What it does |
|---|---|---|---|
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/generate` | editor | US33.4: label-first generation. Queued when the job worker runs (large sets take many LLM calls), otherwise done in this request. |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/oracle/conflicts` | signed in | US33.3. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/oracle/conflicts/report-gap` | editor | US33.3: "the rules don't match practice" becomes a process gap. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/oracle/label` | editor | US33.2. |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/relations` | signed in |  |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/relations` | editor | US33.5. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/relations/{relation_id}/variants` | editor |  |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/review-queue` | signed in | US33.7. |
| GET | `/api/processes/{process_id}/decision-rules` | signed in |  |
| POST | `/api/processes/{process_id}/decision-rules/propose` | editor | US33.1: draft rules for decision points that have none yet. |
| PATCH | `/api/processes/{process_id}/decision-rules/{rule_id}` | editor |  |
| GET | `/api/processes/{process_id}/judge-rubrics` | signed in |  |
| POST | `/api/processes/{process_id}/judge-rubrics` | editor |  |
| PUT | `/api/processes/{process_id}/judge-rubrics/{rubric_id}` | editor | Changing the rubric or judge model voids the calibration. |
| POST | `/api/processes/{process_id}/judge-rubrics/{rubric_id}/calibrate/{dataset_id}` | editor | US33.6. |
| POST | `/api/processes/{process_id}/judge-rubrics/{rubric_id}/judge-run/{run_id}` | editor |  |

### orchestration

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/orchestration/runs` | signed in |  |
| GET | `/api/processes/{process_id}/orchestration/runs/{run_id}` | signed in |  |
| POST | `/api/processes/{process_id}/orchestration/runs/{run_id}/cancel` | editor |  |
| POST | `/api/processes/{process_id}/orchestration/runs/{run_id}/resume` | editor |  |
| GET | `/api/processes/{process_id}/orchestration/scenarios` | signed in |  |
| POST | `/api/processes/{process_id}/orchestration/scenarios` | editor |  |
| DELETE | `/api/processes/{process_id}/orchestration/scenarios/{scenario_id}` | editor |  |
| POST | `/api/processes/{process_id}/orchestration/scenarios/{scenario_id}/run` | editor |  |

### organization

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/context-packs/{pack_id}` | signed in | US28.9: the exact pack an output was produced with. |
| GET | `/api/organization` | signed in |  |
| PUT | `/api/organization` | editor |  |
| GET | `/api/organization/applicable` | signed in |  |
| GET | `/api/organization/audit-log` | signed in | US31.10: group-level access only -- the log itself reveals context. |
| GET | `/api/organization/changes` | signed in | US31.9: incoming context changes, each summarized as impact ("AML-004 v8 -- 3 blueprints, 5 agents, 2 regression suites affected"). |
| GET | `/api/organization/documents` | signed in |  |
| POST | `/api/organization/documents` | editor | One policy document per upload. The policy entity is created (or, when `code` matches an existing policy, re-versioned) immediately; the ext |
| GET | `/api/organization/documents/{document_id}` | signed in |  |
| GET | `/api/organization/documents/{document_id}/passages` | signed in |  |
| GET | `/api/organization/entities` | signed in | `status=all` lists every status; a comma-separated list filters to several (e.g. `proposed,confirmed`). Only context the user may see. |
| POST | `/api/organization/entities` | editor |  |
| POST | `/api/organization/entities/bulk-confirm` | editor |  |
| DELETE | `/api/organization/entities/{entity_id}` | editor | Retire rather than delete: pinned context packs (Epic 28) may still reference this entity's history. |
| GET | `/api/organization/entities/{entity_id}` | signed in |  |
| PUT | `/api/organization/entities/{entity_id}` | editor |  |
| POST | `/api/organization/entities/{entity_id}/confirm` | editor |  |
| POST | `/api/organization/entities/{entity_id}/dismiss` | editor |  |
| GET | `/api/organization/entities/{entity_id}/impact` | signed in | US28.8 / Epic 31 US31.9: which blueprints, agents, and suites were built against this entity. |
| POST | `/api/organization/entities/{entity_id}/rerun-affected` | editor |  |
| GET | `/api/organization/entities/{entity_id}/versions` | signed in |  |
| PUT | `/api/organization/llm-policy` | editor | Epic 31, US31.8: the organization-wide model policy, used where a business unit has none of its own. |
| GET | `/api/organization/members` | signed in | US31.7: who belongs to which business unit. |
| PUT | `/api/organization/members/{user_id}` | editor | Only group-level (unrestricted) editors manage memberships. An empty list makes the user unrestricted again. |
| POST | `/api/organization/profile` | editor |  |
| GET | `/api/organization/starter-packs` | signed in |  |
| POST | `/api/organization/starter-packs/{pack_id}/apply` | editor |  |
| GET | `/api/processes/{process_id}/scope` | signed in |  |
| PUT | `/api/processes/{process_id}/scope` | editor |  |
| GET | `/api/processes/{process_id}/scope/context-pack` | signed in | US28.9: what a call site would receive for this process right now. |

### process-twin

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/process-twin/batches` | signed in |  |
| POST | `/api/processes/{process_id}/process-twin/batches` | editor | US36.5: one case per dataset row, through every agent. |
| GET | `/api/processes/{process_id}/process-twin/batches/{batch_id}` | signed in |  |
| POST | `/api/processes/{process_id}/process-twin/batches/{batch_id}/control` | editor |  |
| GET | `/api/processes/{process_id}/process-twin/batches/{batch_id}/report` | signed in | US36.7: live while running, frozen once finished. |
| POST | `/api/processes/{process_id}/process-twin/estimate` | signed in |  |
| GET | `/api/processes/{process_id}/process-twin/operating-model` | signed in | US36.4: the saved model, or one pre-filled from the diagram. |
| PUT | `/api/processes/{process_id}/process-twin/operating-model` | editor |  |

### processes

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes` | signed in |  |
| POST | `/api/processes` | editor |  |
| DELETE | `/api/processes/{process_id}` | editor |  |
| GET | `/api/processes/{process_id}` | signed in |  |

### publish

| Method | Path | Access | What it does |
|---|---|---|---|
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/publications/{publication_id}/mark-deployed` | editor |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/publish` | editor |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/publish-status` | signed in |  |

### registries

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/registries` | signed in |  |
| GET | `/api/registries/search` | signed in |  |
| POST | `/api/registries/{registry_name}/push` | editor |  |

### results

| Method | Path | Access | What it does |
|---|---|---|---|
| DELETE | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/release-criteria` | editor |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/release-criteria` | signed in |  |
| PUT | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/release-criteria` | editor | US35.7: what "good enough to release" means for this agent. |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/release-gate` | signed in |  |
| GET | `/api/processes/{process_id}/batch-comparison` | signed in | US35.6: paired on the cases both batches ran. |
| GET | `/api/processes/{process_id}/batches/{batch_id}/evidence` | signed in | US35.9. |
| GET | `/api/processes/{process_id}/batches/{batch_id}/results` | signed in | US35.1-US35.5, US35.8 -- partial while the batch is still running. |

### twin

| Method | Path | Access | What it does |
|---|---|---|---|
| DELETE | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/baseline` | editor |  |
| PUT | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/baseline` | editor |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/runs` | signed in |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/scenarios` | signed in |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/scenarios` | editor |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/authority-personas` | signed in | Epic 30, US30.3: reviewers straight from the approval-authority matrix -- deterministic, so not a paid LLM call and open to viewers. |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/context` | signed in |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/data` | editor |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/describe` | editor |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/personas` | editor |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/suggest` | editor |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/twin-summary` | signed in |  |
| DELETE | `/api/processes/{process_id}/scenarios/{scenario_id}` | editor |  |
| POST | `/api/processes/{process_id}/scenarios/{scenario_id}/run` | editor | Epic 17, US17.6: same two-step BackgroundTask handoff app/api/orchestration.py's `start_run` already uses -- a twin run used to execute sync |
| GET | `/api/processes/{process_id}/twin-runs/{run_id}` | signed in |  |
| POST | `/api/processes/{process_id}/twin-runs/{run_id}/cancel` | editor |  |
| POST | `/api/processes/{process_id}/twin-runs/{run_id}/resume` | editor |  |

### versions

| Method | Path | Access | What it does |
|---|---|---|---|
| POST | `/api/processes/{process_id}/finalize` | editor |  |
| GET | `/api/processes/{process_id}/versions` | signed in |  |
| GET | `/api/processes/{process_id}/versions/diff` | signed in |  |
| GET | `/api/processes/{process_id}/versions/{version_id}` | signed in |  |
| POST | `/api/processes/{process_id}/versions/{version_id}/restore` | editor |  |
<!-- END GENERATED: api-catalogue -->
