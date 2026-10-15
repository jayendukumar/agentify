# API reference

Generated from the OpenAPI document the API serves (`GET /openapi.json`;
interactive docs at `http://localhost:8000/docs`). Every route except
`/healthz` and login needs a session cookie from `POST /api/auth/login`.

<!-- BEGIN GENERATED: api-catalogue -->
**199 endpoints.** Access: a `permission` (granted by roles, checked against the process's business unit -- see Glossary); *signed in* needs any session; *public* needs none.

### agents

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/blueprint/agent-artifacts` | signed in |  |
| GET | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/approval` | signed in | Epic 37, US37.3/US37.4: who changed this version, who approved it, and whether the signed-in user may approve it (and if not, why). |
| POST | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/approve` | `agent.approve` | Epic 37, US37.4/US37.5: refused for anyone who made a high-risk change to this agent since its last approval; APPROVED once the required num |
| GET | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/compliance-evidence` | signed in | Epic 30, US30.7. |
| GET | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/compliance-evidence/export` | signed in |  |
| PATCH | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/governance` | `agent.build` | Epic 20, US20.1: editor-only, same tier as generate/publish (Epic 15's US15.6 precedent) -- widening an agent's declared permissions is at l |
| POST | `/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}/mark-release-candidate` | `agent.release_candidate` | Epic 23, US23.4: editor-only, same tier as governance/publish. |
| POST | `/api/processes/{process_id}/blueprint/nodes/{node_id}/agent-artifact` | `agent.build` |  |

### auth

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/auth/config` | public | Epic 37: public -- the sign-in page needs it before anyone signs in. |
| POST | `/api/auth/login` | public | Local sign-in: a name and self-picked roles. Settings validation refuses AUTH_MODE=local in production (US37.14), so this endpoint never ser |
| POST | `/api/auth/logout` | signed in | Ends this session. After an SSO sign-in it also returns the IdP's logout URL, so the IdP session ends too -- otherwise the next "Sign in" wo |
| GET | `/api/auth/me` | signed in |  |
| POST | `/api/auth/oidc/backchannel-logout` | public | US37.13: the IdP says a user signed out there (or an admin ended their IdP session). Authenticated by the logout token's signature, not by a |
| GET | `/api/auth/oidc/callback` | public | Finishes a sign-in. Every refusal is returned (not raised) so its audit event commits. |
| GET | `/api/auth/oidc/login` | public | Starts a sign-in: remembers a fresh state, nonce and PKCE verifier, binds the state to this browser with a short-lived cookie, and sends the |
| POST | `/api/auth/refresh` | public | US37.12: swaps the refresh cookie for a new pair. No CSRF header: after a page reload the frontend doesn't have the token yet -- this is how |
| GET | `/api/auth/sessions` | signed in | US37.13: a user sees where they're signed in. |
| DELETE | `/api/auth/sessions/{session_id}` | signed in | US37.13: a user ends one of their own sessions -- this one included, which signs them out here. |

### batches

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/batch-schedules` | signed in |  |
| POST | `/api/processes/{process_id}/batch-schedules` | `agent.test` |  |
| DELETE | `/api/processes/{process_id}/batch-schedules/{schedule_id}` | `agent.test` |  |
| GET | `/api/processes/{process_id}/batches` | signed in |  |
| GET | `/api/processes/{process_id}/batches/{batch_id}` | signed in |  |
| POST | `/api/processes/{process_id}/batches/{batch_id}/control` | `agent.test` | US34.7. |
| POST | `/api/processes/{process_id}/batches/{batch_id}/runs/{run_id}/replay` | `agent.test` | US34.6: the same row, trial, and seed -- on its own. |
| POST | `/api/processes/{process_id}/scenarios/{scenario_id}/batches` | `agent.test` |  |
| POST | `/api/processes/{process_id}/scenarios/{scenario_id}/batches/estimate` | `agent.test` | US34.3: what a batch would cost and take, before starting it. |

### blueprint

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/blueprint` | signed in |  |
| GET | `/api/processes/{process_id}/blueprint/export` | signed in |  |
| POST | `/api/processes/{process_id}/blueprint/generate` | `blueprint.edit` |  |
| PATCH | `/api/processes/{process_id}/blueprint/nodes/{node_id}` | `blueprint.edit` |  |

### bpmn

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/bpmn` | signed in |  |
| PUT | `/api/processes/{process_id}/bpmn` | `process.edit` |  |
| POST | `/api/processes/{process_id}/bpmn/generate` | `process.edit` |  |

### catalog

| Method | Path | Access | What it does |
|---|---|---|---|
| POST | `/api/organization/applications/import` | `catalog.edit` | US29.2: preview first (`dry_run=true`, the default), then import. |
| GET | `/api/organization/applications/register` | signed in | US29.8. |
| PUT | `/api/organization/applications/{application_id}/api-spec` | `catalog.edit` | US29.4: attach an OpenAPI document (JSON or YAML). |
| PUT | `/api/organization/applications/{application_id}/endpoints` | `catalog.edit` | US29.5: register endpoints. A production-tagged endpoint may be recorded for reference but is never called. Any other endpoint must pass the |
| GET | `/api/processes/{process_id}/systems` | signed in | US29.3: every system name in the process and what it resolves to. Exact matches are linked automatically on read. |
| PUT | `/api/processes/{process_id}/systems/{system_name}` | `agent.build` |  |

### chat

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/chat/messages` | signed in |  |
| POST | `/api/processes/{process_id}/chat/messages` | `process.edit` |  |
| POST | `/api/processes/{process_id}/chat/messages/{message_id}/apply` | `process.edit` |  |

### connectors

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/organization/connectors` | signed in |  |
| POST | `/api/organization/connectors` | `org.admin` |  |
| GET | `/api/organization/connectors/types` | signed in |  |
| DELETE | `/api/organization/connectors/{connector_id}` | `org.admin` | Deleting a connector keeps what it synced (as ordinary context with its connector source) -- removing context is a separate decision. |
| PUT | `/api/organization/connectors/{connector_id}` | `org.admin` |  |
| GET | `/api/organization/connectors/{connector_id}/runs` | signed in |  |
| POST | `/api/organization/connectors/{connector_id}/sync` | `org.admin` | Queued when the job worker runs; otherwise synced in this request. |

### datasets

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/datasets` | signed in |  |
| POST | `/api/processes/{process_id}/datasets` | `agent.test` |  |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}` | signed in |  |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/diff` | signed in | US32.6. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/holdout` | `agent.test` | US32.8. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/import` | `agent.test` | US32.3, step 2: import with the confirmed mapping. Columns marked hash/fake are pseudonymized locally before anything is stored. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/import/preview` | `agent.test` | US32.3, step 1: read the file and suggest a column mapping. Nothing is stored. |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/rows` | signed in | US32.8: holdout rows' labels are hidden unless `include_holdout` -- so they aren't looked at while the agent is being tuned. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/rows` | `agent.test` |  |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/rows/from-run` | `agent.test` | US32.7: a failed or surprising run becomes a golden case. |
| DELETE | `/api/processes/{process_id}/datasets/{dataset_id}/rows/{row_key}` | `agent.test` |  |
| PATCH | `/api/processes/{process_id}/datasets/{dataset_id}/rows/{row_key}` | `agent.test` | US32.4: correct or confirm a label (recorded with who did it). |
| PUT | `/api/processes/{process_id}/datasets/{dataset_id}/rows/{row_key}/ambiguity` | `agent.test` | US32.5. |

### documents

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/documents` | signed in |  |
| POST | `/api/processes/{process_id}/documents` | `process.edit` |  |
| DELETE | `/api/processes/{process_id}/documents/{document_id}` | `process.edit` |  |
| GET | `/api/processes/{process_id}/documents/{document_id}` | signed in |  |

### environments

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/twin-environments` | signed in |  |
| POST | `/api/processes/{process_id}/twin-environments` | `agent.test` |  |
| DELETE | `/api/processes/{process_id}/twin-environments/{environment_id}` | `agent.test` |  |
| PUT | `/api/processes/{process_id}/twin-environments/{environment_id}` | `agent.test` |  |

### evaluation

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/regression-suite-runs/{suite_run_id}` | signed in |  |
| GET | `/api/processes/{process_id}/regression-suites` | signed in |  |
| POST | `/api/processes/{process_id}/regression-suites` | `agent.test` |  |
| DELETE | `/api/processes/{process_id}/regression-suites/{suite_id}` | `agent.test` |  |
| POST | `/api/processes/{process_id}/regression-suites/{suite_id}/run` | `agent.test` | US19.3: same two-step create-then-enqueue handoff as app/api/twin.py's/app/api/orchestration.py's `start_run` -- one background task per sce |
| GET | `/api/processes/{process_id}/runs/compare` | signed in |  |
| GET | `/api/processes/{process_id}/runs/{run_id}/snapshot` | signed in | Epic 22, US22.1/US22.2/US22.3. |

### gap-analysis

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/gap-findings` | signed in |  |
| POST | `/api/processes/{process_id}/gap-findings/analyze` | `process.edit` |  |
| POST | `/api/processes/{process_id}/gap-findings/{finding_id}/dismiss` | `process.edit` |  |
| POST | `/api/processes/{process_id}/gap-findings/{finding_id}/resolve` | `process.edit` |  |

### governance

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/audit-events` | `audit.read` |  |
| GET | `/api/audit-events/export` | `audit.read` |  |
| GET | `/api/organization/governance` | signed in |  |
| PUT | `/api/organization/governance` | `org.admin` |  |
| GET | `/api/users` | `users.manage` |  |
| POST | `/api/users/{user_id}/activate` | `users.manage` |  |
| POST | `/api/users/{user_id}/deactivate` | `users.manage` | Deactivated users keep their history but can't sign in; every session they hold ends now. |
| PUT | `/api/users/{user_id}/roles` | `users.manage` |  |
| GET | `/api/users/{user_id}/sessions` | `users.manage` | US37.13: where a user is signed in right now. |
| POST | `/api/users/{user_id}/sessions/revoke-all` | `users.manage` | US37.13: "Sign out everywhere" -- for a lost laptop or an incident, without waiting for the IdP. |
| DELETE | `/api/users/{user_id}/sessions/{session_id}` | `users.manage` | US37.13: ends one session; it stops working on its next request. |

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
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/generate` | `agent.test` | US33.4: label-first generation. Queued when the job worker runs (large sets take many LLM calls), otherwise done in this request. |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/oracle/conflicts` | signed in | US33.3. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/oracle/conflicts/report-gap` | `agent.test` | US33.3: "the rules don't match practice" becomes a process gap. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/oracle/label` | `agent.test` | US33.2. |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/relations` | signed in |  |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/relations` | `agent.test` | US33.5. |
| POST | `/api/processes/{process_id}/datasets/{dataset_id}/relations/{relation_id}/variants` | `agent.test` |  |
| GET | `/api/processes/{process_id}/datasets/{dataset_id}/review-queue` | signed in | US33.7. |
| GET | `/api/processes/{process_id}/decision-rules` | signed in |  |
| POST | `/api/processes/{process_id}/decision-rules/propose` | `agent.test` | US33.1: draft rules for decision points that have none yet. |
| PATCH | `/api/processes/{process_id}/decision-rules/{rule_id}` | `agent.test` |  |
| GET | `/api/processes/{process_id}/judge-rubrics` | signed in |  |
| POST | `/api/processes/{process_id}/judge-rubrics` | `agent.test` |  |
| PUT | `/api/processes/{process_id}/judge-rubrics/{rubric_id}` | `agent.test` | Changing the rubric or judge model voids the calibration. |
| POST | `/api/processes/{process_id}/judge-rubrics/{rubric_id}/calibrate/{dataset_id}` | `agent.test` | US33.6. |
| POST | `/api/processes/{process_id}/judge-rubrics/{rubric_id}/judge-run/{run_id}` | `agent.test` |  |

### orchestration

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/orchestration/runs` | signed in |  |
| GET | `/api/processes/{process_id}/orchestration/runs/{run_id}` | signed in |  |
| POST | `/api/processes/{process_id}/orchestration/runs/{run_id}/cancel` | `agent.test` |  |
| POST | `/api/processes/{process_id}/orchestration/runs/{run_id}/resume` | `agent.test` |  |
| GET | `/api/processes/{process_id}/orchestration/scenarios` | signed in |  |
| POST | `/api/processes/{process_id}/orchestration/scenarios` | `agent.test` |  |
| DELETE | `/api/processes/{process_id}/orchestration/scenarios/{scenario_id}` | `agent.test` |  |
| POST | `/api/processes/{process_id}/orchestration/scenarios/{scenario_id}/run` | `agent.test` |  |

### organization

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/context-packs/{pack_id}` | signed in | US28.9: the exact pack an output was produced with. |
| GET | `/api/organization` | signed in |  |
| PUT | `/api/organization` | `org.admin` |  |
| GET | `/api/organization/applicable` | signed in |  |
| GET | `/api/organization/audit-log` | signed in | US31.10: group-level access only -- the log itself reveals context. |
| GET | `/api/organization/changes` | signed in | US31.9: incoming context changes, each summarized as impact ("AML-004 v8 -- 3 blueprints, 5 agents, 2 regression suites affected"). |
| GET | `/api/organization/documents` | signed in |  |
| POST | `/api/organization/documents` | `context.edit` | One policy document per upload. The policy entity is created (or, when `code` matches an existing policy, re-versioned) immediately; the ext |
| GET | `/api/organization/documents/{document_id}` | signed in |  |
| GET | `/api/organization/documents/{document_id}/passages` | signed in |  |
| GET | `/api/organization/entities` | signed in | `status=all` lists every status; a comma-separated list filters to several (e.g. `proposed,confirmed`). Only context the user may see. |
| POST | `/api/organization/entities` | `context.edit` |  |
| POST | `/api/organization/entities/bulk-confirm` | `context.confirm` |  |
| DELETE | `/api/organization/entities/{entity_id}` | `context.edit` | Retire rather than delete: pinned context packs (Epic 28) may still reference this entity's history. |
| GET | `/api/organization/entities/{entity_id}` | signed in |  |
| PUT | `/api/organization/entities/{entity_id}` | `context.edit` |  |
| POST | `/api/organization/entities/{entity_id}/confirm` | `context.confirm` |  |
| POST | `/api/organization/entities/{entity_id}/dismiss` | `context.edit` |  |
| GET | `/api/organization/entities/{entity_id}/impact` | signed in | US28.8 / Epic 31 US31.9: which blueprints, agents, and suites were built against this entity. |
| POST | `/api/organization/entities/{entity_id}/rerun-affected` | `context.edit` |  |
| GET | `/api/organization/entities/{entity_id}/versions` | signed in |  |
| PUT | `/api/organization/llm-policy` | `org.admin` | Epic 31, US31.8: the organization-wide model policy, used where a business unit has none of its own. |
| GET | `/api/organization/members` | signed in | US31.7: who belongs to which business unit. |
| PUT | `/api/organization/members/{user_id}` | `org.admin` | Only group-level (unrestricted) editors manage memberships. An empty list makes the user unrestricted again. |
| POST | `/api/organization/profile` | `context.edit` |  |
| GET | `/api/organization/starter-packs` | signed in |  |
| POST | `/api/organization/starter-packs/{pack_id}/apply` | `context.edit` |  |
| GET | `/api/processes/{process_id}/scope` | signed in |  |
| PUT | `/api/processes/{process_id}/scope` | `process.edit` |  |
| GET | `/api/processes/{process_id}/scope/context-pack` | signed in | US28.9: what a call site would receive for this process right now. |

### process-twin

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes/{process_id}/process-twin/batches` | signed in |  |
| POST | `/api/processes/{process_id}/process-twin/batches` | `agent.test` | US36.5: one case per dataset row, through every agent. |
| GET | `/api/processes/{process_id}/process-twin/batches/{batch_id}` | signed in |  |
| POST | `/api/processes/{process_id}/process-twin/batches/{batch_id}/control` | `agent.test` |  |
| GET | `/api/processes/{process_id}/process-twin/batches/{batch_id}/report` | signed in | US36.7: live while running, frozen once finished. |
| POST | `/api/processes/{process_id}/process-twin/estimate` | `agent.test` |  |
| GET | `/api/processes/{process_id}/process-twin/operating-model` | signed in | US36.4: the saved model, or one pre-filled from the diagram. |
| PUT | `/api/processes/{process_id}/process-twin/operating-model` | `agent.test` |  |

### processes

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/processes` | signed in |  |
| POST | `/api/processes` | `process.edit` |  |
| DELETE | `/api/processes/{process_id}` | `process.delete` |  |
| GET | `/api/processes/{process_id}` | signed in |  |

### publish

| Method | Path | Access | What it does |
|---|---|---|---|
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/publications/{publication_id}/mark-deployed` | `agent.publish` |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/publish` | `agent.publish` |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/publish-status` | signed in |  |

### registries

| Method | Path | Access | What it does |
|---|---|---|---|
| GET | `/api/registries` | signed in |  |
| GET | `/api/registries/search` | signed in |  |
| POST | `/api/registries/{registry_name}/push` | `agent.publish` |  |

### results

| Method | Path | Access | What it does |
|---|---|---|---|
| DELETE | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/release-criteria` | `agent.release_criteria` |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/release-criteria` | signed in |  |
| PUT | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/release-criteria` | `agent.release_criteria` | US35.7: what "good enough to release" means for this agent. |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/release-gate` | signed in |  |
| GET | `/api/processes/{process_id}/batch-comparison` | signed in | US35.6: paired on the cases both batches ran. |
| GET | `/api/processes/{process_id}/batches/{batch_id}/evidence` | signed in | US35.9. |
| GET | `/api/processes/{process_id}/batches/{batch_id}/results` | signed in | US35.1-US35.5, US35.8 -- partial while the batch is still running. |

### twin

| Method | Path | Access | What it does |
|---|---|---|---|
| DELETE | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/baseline` | `agent.test` |  |
| PUT | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/baseline` | `agent.test` |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/runs` | signed in |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/scenarios` | signed in |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/scenarios` | `agent.test` |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/authority-personas` | signed in | Epic 30, US30.3: reviewers straight from the approval-authority matrix -- deterministic, so not a paid LLM call and open to viewers. |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/context` | signed in |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/data` | `agent.test` |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/describe` | `agent.test` |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/personas` | `agent.test` |  |
| POST | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder/suggest` | `agent.test` |  |
| GET | `/api/processes/{process_id}/agent-artifacts/{artifact_id}/twin-summary` | signed in |  |
| DELETE | `/api/processes/{process_id}/scenarios/{scenario_id}` | `agent.test` |  |
| POST | `/api/processes/{process_id}/scenarios/{scenario_id}/run` | `agent.test` | Epic 17, US17.6: same two-step BackgroundTask handoff app/api/orchestration.py's `start_run` already uses -- a twin run used to execute sync |
| GET | `/api/processes/{process_id}/twin-runs/{run_id}` | signed in |  |
| POST | `/api/processes/{process_id}/twin-runs/{run_id}/cancel` | `agent.test` |  |
| POST | `/api/processes/{process_id}/twin-runs/{run_id}/resume` | `agent.test` |  |

### versions

| Method | Path | Access | What it does |
|---|---|---|---|
| POST | `/api/processes/{process_id}/finalize` | `process.finalize` |  |
| GET | `/api/processes/{process_id}/versions` | signed in |  |
| GET | `/api/processes/{process_id}/versions/diff` | signed in |  |
| GET | `/api/processes/{process_id}/versions/{version_id}` | signed in |  |
| GET | `/api/processes/{process_id}/versions/{version_id}/approval` | signed in |  |
| POST | `/api/processes/{process_id}/versions/{version_id}/approve` | `process.approve` | Makes this version the baseline. Refused for anyone who made a high-risk change to the process since the previous approved baseline. |
| POST | `/api/processes/{process_id}/versions/{version_id}/reject` | `process.approve` |  |
| POST | `/api/processes/{process_id}/versions/{version_id}/restore` | `process.edit` |  |
<!-- END GENERATED: api-catalogue -->
