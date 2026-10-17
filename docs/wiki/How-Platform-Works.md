# How the Platform Works

## What happens when a document is uploaded ?

Two separate phases make the diagram. Uploading a document only extracts and merges a structured process model, and that is the only step that calls the LLM. The BPMN XML is built later, when the user clicks **Generate BPMN** (or **Refresh Layout**), by plain code with no LLM call.

### Phase 1: Upload and ingestion (runs automatically in the background)

**1. Upload endpoint**: `POST /api/processes/{id}/documents` (`backend/app/api/documents.py:35`)
- It rejects unsupported file types (PDF, DOCX, Visio `.vsdx`, PNG and JPEG are accepted) and files over 25 MB.
- It saves the raw file under the tenant's own directory (`ingestion/storage.py`).
- It inserts a `document` row and commits straight away, so the background task, which opens its own DB session, can see that row.
- It queues `pipeline.process_document` as a FastAPI BackgroundTask and returns `201` at once.

**2. Background pipeline** (`backend/app/ingestion/pipeline.py:162`)
1. **Model routing**: picks the LLM route from the business unit's model policy (US31.8).
2. The document's status is set to `processing`.
3. **Text extraction with plain code** (`extractors.py`), which outputs `ExtractedBlock(kind, location, content)`:
   - **PDF** (pdfplumber): one text block per page, plus each table rendered as `a | b | c` rows.
   - **DOCX** (python-docx): one block per paragraph, plus tables.
   - **Visio**: the zip's XML is parsed. Shape text becomes blocks, and connector topology becomes explicit `Connector: 'X' -> 'Y'` blocks, so the LLM doesn't have to guess flow from where shapes sit on the page.
   - **Images**: this step is skipped, and the image goes straight to the vision model.
4. **Organization vocabulary**: `resolve_for_process` builds a Context Pack of glossary terms and application names, so the extracted actors and systems use the organization's own names.
5. **LLM structuring** (`structuring.py`): one call covers the whole document, with no chunking.
   - Each block is tagged `[SOURCE <location>]` so the model can cite where each element came from.
   - The model returns JSON with `actors`, `elements` (task, decision, start, end or intermediate event, each with a confidence and `source_refs`) and `flows`. It also returns `is_process_definition` and a confidence score.
   - If the model leaves out the classification fields, which happens often with the live model, up to two small follow-up calls ask only that yes/no question.
   - The output is then cleaned up in code:
     - stray `SOURCE` markers are stripped and `document_id` is forced to the real value;
     - confidence values given as 0–1 are rescaled to 0–100;
     - the LLM's local IDs (`el-1`, `actor-1`) are replaced with globally unique ones;
     - any reference to an undeclared actor is set to null;
     - any flow pointing at a missing element is dropped.
   - If the document isn't classified as a process, this raises `DocumentValidationError` and the document is marked `failed`.
6. **Merge into the process schema** (`repository.merge_process_schema` → `ingestion/merge.py`):
   - Actors are deduplicated by name and type.
   - Two elements whose labels are at least 85% similar (difflib) are treated as one step, and their sources, inputs, outputs and systems are combined.
   - If the labels match but the actors disagree, both elements are kept and both are downgraded to `low` confidence.
   - Flows are remapped to the merged elements, and exact duplicates are removed.
   - A `ProcessSchemaChange` audit row is written.
7. **Embeddings**: extracted blocks are embedded with a local sentence-transformers model.
8. **Gap analysis** (Epic 11): best effort only. If it fails, the document doesn't fail.
9. The status is set to `done`. If any error is raised, the document is set to `failed` with the error message and its validation result.

### Phase 2: BPMN generation (when the user asks)

**Endpoint**: `POST /api/processes/{id}/bpmn/generate` (`backend/app/api/bpmn.py:25`). The UI calls it from `ProcessDetailPage.tsx:135` and from "Refresh Layout" in `DiagramPage.tsx:96`.

1. It loads the **current merged schema**. `document_ids` in the request isn't used as a filter. It returns `400` if no elements have been extracted yet.
2. **Mapping** (`bpmn/mapping.py`):
   - Each actor becomes a **lane** in a single pool.
   - Each `task` becomes a `userTask` (role actor), `serviceTask` (system actor) or plain `task`.
   - Each `decision` becomes a gateway, chosen from its outgoing conditions:
     - every branch has a condition: **exclusive**;
     - no branch has one: **parallel**;
     - a mix: **inclusive**.
   - Events become `startEvent`, `endEvent` or `intermediateCatchEvent`.
   - Any element whose confidence isn't `high` is flagged as low confidence.
3. **Layout** (`bpmn/layout.py`):
   - **X position**: rank from Kahn's algorithm, starting at the start events. Nodes inside rework loops get a fallback rank, so cycles can't hang the layout.
   - **Y position**: a fixed vertical band per lane.
   - **Lane bounds**: computed so bpmn-js actually draws the swimlanes.
   - **Edges**: routed at right angles. Loop-back edges run below the whole diagram.
4. **XML build** (`bpmn/builder.py`): writes BPMN 2.0 `definitions`, `process`, `laneSet`, the nodes, and `sequenceFlow`s with `conditionExpression` where a condition exists. It also writes the full DI section (shapes, bounds, edge waypoints) so the diagram can render.
5. **Persist**: `set_draft_bpmn` stores the XML and the low-confidence element IDs as an audited `draft_generated` change.
6. **Validate** (`bpmn/validation.py`): this runs fresh on every read. It checks for:
   - duplicate IDs;
   - dangling flow references;
   - nodes with no incoming flow that aren't start events, and nodes with no outgoing flow that aren't end events;
   - missing or dangling DI.

   On a generated draft, the issues are returned in the response alongside the XML and don't block it. A manual `PUT` with issues is rejected.

The frontend renders the XML with bpmn-js and highlights the low-confidence elements for review. Chat edits made after that (`bpmn/chat_ops.py`) change the schema and rebuild the diagram, keeping existing node positions.

## The LLM structuring prompts

These are the actual prompts `backend/app/ingestion/structuring.py` sends. Each one goes as a single `user` message with these settings:

| Call | `operation` | `response_format` | `max_tokens` |
|---|---|---|---|
| Main extraction (document or image) | `document_extraction` | `{"type": "json_object"}` | 16000 |
| Classification follow-up | `document_extraction` | `{"type": "json_object"}` | 2000 |

### 1. Document prompt (PDF / DOCX / Visio)

The prompt is put together in `structure_process_with_validation` in four parts:

1. `_TEXT_INSTRUCTIONS`, with `_SCHEMA_CONTRACT` filled in.
2. The organization vocabulary pack, but only when it isn't empty.
3. The document id and filename.
4. Each extracted block, rendered as `[SOURCE <location>]\n<content>`, with a blank line between blocks.

The text below was rendered straight from the code. Values in `<angle brackets>` are filled in for each document.

```text
You are extracting a structured business process description from a source document for the Agentic Solution Generator project.

Read the document content below (each chunk is marked with a [SOURCE <location>] header showing where it came from in the original file) and identify the business process it describes: the sequence of activities/steps, the actors or systems that perform them, decision points, and the inputs/outputs/systems each step touches.

Respond with a single JSON object matching exactly this shape (no prose, no markdown code fences, just the JSON object):
{
  "is_process_definition": true,
  "process_definition_confidence": 0,
  "validation_message": "One or two concise sentences explaining the evidence for the decision.",
  "actors": [
    {"id": "string, unique, e.g. actor-1", "name": "string", "type": "role" | "system" | "external_party"}
  ],
  "elements": [
    {
      "id": "string, unique, e.g. el-1",
      "type": "task" | "decision" | "start_event" | "end_event" | "intermediate_event",
      "label": "string, concise description of the step",
      "actor_id": "string matching an actors[].id, or null",
      "inputs": ["string"],
      "outputs": ["string"],
      "systems_touched": ["string"],
      "source_refs": [
        {"document_id": "<the document id given below, exactly>", "location": "<the location text only -- for text documents, copy what appears between 'SOURCE ' and the closing ']' in the matching marker, WITHOUT the brackets or the word SOURCE; for an image, just the word 'image'>", "excerpt": "short quote or description supporting this element"}
      ],
      "confidence": "high" | "medium" | "low"
    }
  ],
  "flows": [
    {"id": "string, unique, e.g. f-1", "from": "an elements[].id", "to": "an elements[].id", "condition": "string or null"}
  ]
}

Before extracting entities, validate whether this document is actually a process definition. A process definition describes how work is performed: an ordered or connected sequence of activities, decisions, events, inputs/outputs, roles, or systems. Project backlogs, issue lists, roadmaps, meeting notes, status reports, and generic requirement inventories are not process definitions unless they also clearly describe how work flows from start to finish.

Rules:
- Every element must have at least one source_ref. Never invent an element that isn't evidenced in the document content.
- confidence "high": the element and its position in the sequence are explicitly and unambiguously stated in one clear passage. "medium": some detail (actor, trigger, exact position) was inferred rather than explicit. "low": inferred from indirect evidence, or the document is ambiguous about it. Do not default everything to "high".
- Include start/end events only if the sequencing is clear enough to identify them; omit them rather than guessing.
- If the document does not describe a business process at all, return {"actors": [], "elements": [], "flows": []}.

## Organization vocabulary
The organization's own names for systems and terms mentioned here.

[<label>] <glossary term or application name and its text>
[<label>] <...>

When the document refers to a system or term listed above, use that listed name.

Document id: "<doc_...>"
Document filename: "<filename.pdf>"

Document content:
[SOURCE page 1]
<text of page 1>

[SOURCE page 1, table 1]
<cell | cell | cell>
<cell | cell | cell>

[SOURCE page 2]
<text of page 2>
```

The `## Organization vocabulary` section, through the line "When the document refers to a system or term listed above, use that listed name.", is left out when the process has no matching vocabulary. Location labels differ by format:

| Format | Location labels |
|---|---|
| PDF | `page N`, `page N, table M` |
| DOCX | `paragraph N`, `table M` |
| Visio | `page N, shape ID`, `page N, connector ID` (content `Connector: 'X' -> 'Y'`) |

### 2. Image prompt (PNG / JPEG)

This prompt is sent as two content blocks: a text block, then the image block (`ImageContent.from_bytes`, base64 with its MIME type). No extracted text and no organization vocabulary are included.

```text
You are extracting a structured business process description from a photo or screenshot of a process flowchart/whiteboard for the Agentic Solution Generator project.

Look at the image and identify the business process it depicts: the sequence of activities/steps (boxes, diamonds, or other shapes), the actors or systems that perform them (if labeled), decision points, and any visible inputs/outputs/systems.

Respond with a single JSON object matching exactly this shape (no prose, no markdown code fences, just the JSON object):
{
  "is_process_definition": true,
  "process_definition_confidence": 0,
  "validation_message": "One or two concise sentences explaining the evidence for the decision.",
  "actors": [
    {"id": "string, unique, e.g. actor-1", "name": "string", "type": "role" | "system" | "external_party"}
  ],
  "elements": [
    {
      "id": "string, unique, e.g. el-1",
      "type": "task" | "decision" | "start_event" | "end_event" | "intermediate_event",
      "label": "string, concise description of the step",
      "actor_id": "string matching an actors[].id, or null",
      "inputs": ["string"],
      "outputs": ["string"],
      "systems_touched": ["string"],
      "source_refs": [
        {"document_id": "<the document id given below, exactly>", "location": "<the location text only -- for text documents, copy what appears between 'SOURCE ' and the closing ']' in the matching marker, WITHOUT the brackets or the word SOURCE; for an image, just the word 'image'>", "excerpt": "short quote or description supporting this element"}
      ],
      "confidence": "high" | "medium" | "low"
    }
  ],
  "flows": [
    {"id": "string, unique, e.g. f-1", "from": "an elements[].id", "to": "an elements[].id", "condition": "string or null"}
  ]
}

First validate whether the image actually depicts a process definition or flow. A project board, backlog, roadmap, status report, or generic list of work items is not a process definition unless it clearly shows how work flows from start to finish.

Rules:
- Every element must have at least one source_ref with location "image" and an excerpt describing what you saw (e.g. the shape's label text) --  not a document quote, since there's no text document, just this image.
- Never invent an element that isn't visibly depicted in the image.
- confidence should reflect how legible/clear the image was for that specific element -- "high" only for a clearly legible label with an unambiguous connection; "low" if text was blurry/ambiguous or a connection was guessed rather than following a visible arrow/line.
- Connections between shapes are only reliable evidence when there's a clear arrow or line visible -- do not infer flow purely from spatial proximity or reading order.
- If the image does not depict a business process at all, return {"actors": [], "elements": [], "flows": []}.

Document id: "<doc_...>"
Document filename: "<filename.png>"
```

### 3. Classification follow-up (only when the main response omits `is_process_definition`)

This call reuses the **whole original prompt**: the same document text, or the same text block plus image. It appends the text below to it and sends everything again, up to 2 more times (`_MAX_CLASSIFICATION_ATTEMPTS = 3`, counting the main call). The elements from the first call are kept; only the classification fields are taken from this answer.

```text
<entire original prompt from section 1 or 2>

Ignore the JSON schema above. Answer ONLY this question about the same document/image: does it describe a business process -- an ordered or connected sequence of activities, decisions, events, inputs/outputs, roles, or systems showing how work flows from start to finish? Project backlogs, issue lists, roadmaps, meeting notes, status reports, and generic requirement inventories are NOT process definitions unless they also clearly describe how work flows from start to finish.

Respond with a single JSON object matching exactly this shape (no prose, no markdown code fences). All three fields are required -- never omit any of them:
{
  "is_process_definition": true,
  "process_definition_confidence": 0,
  "validation_message": "One or two concise sentences explaining the evidence for the decision."
}
```

If the classification is still missing after every attempt, the document is treated as **not** a process definition (confidence 0) and is marked `failed`.

## What happens when user clicks Generate agentic blueprint ?

The blueprint judges every step of an **approved** process diagram for AI-agent automation and proposes an agent for each step that can be automated. Only the LLM evaluation calls the model. Everything before it (checks, loading the context) and after it (enforcing rules, saving) is plain code.

**1. Frontend** (`frontend/src/pages/BlueprintPage.tsx`, `handleGenerate`)
- If a blueprint already exists, the user must confirm first. Regenerating replaces the current recommendations and any manual overrides.
- It calls `generateBlueprint()` (`frontend/src/api/client.ts`), which sends `POST /api/processes/{id}/blueprint/generate` with `{ "version_id": null }`. `null` means "use the latest version".

**2. Endpoint checks** (`backend/app/api/blueprint.py`, `generate_blueprint`)
- The user needs the `BLUEPRINT_EDIT` permission (`BlueprintEditDep`).
- It returns `404` if the process doesn't exist.
- It loads the requested version, or the latest one. It returns `400` if there is none, or if its status isn't `approved` (Epic 37, US37.4). Draft and pending versions are never evaluated.
- `extract_flow_nodes(version.xml)` parses the BPMN into steps. Each step has its ID, label, BPMN type, lane (actor), and the steps before and after it with any flow conditions. It returns `400` if there are no steps.

**3. Organization context** (Epic 28, US28.5)
- `resolve_for_process(purpose="blueprint_evaluation", ...)` builds a **Context Pack**: the rules, controls, roles and policy passages that apply to the process's scope. Every step's `label (lane)` is used as the search query.
- `render_pack()` turns the pack into the `## Organization context` prompt section, in which each item has a bracketed label (`[C3]`, `[P1]`) that the model cites back. If no context applies, the section is empty.

**4. Model routing**: `llm_route_for(db, process_id=...)` applies the business unit's model policy (provider, model, redaction) to every LLM call in the request.

**5. LLM evaluation** (`backend/app/blueprint/service.py`, `evaluate_blueprint`)
- **Chunking**: a diagram with up to 10 steps goes in one call. Larger diagrams are split into chunks of 8 (`CHUNK_SIZE`), evaluated **concurrently** with `asyncio.gather`. Each chunk's prompt also lists the rest of the diagram, for context only. This exists because a 20-step diagram in a single call was cut off mid-JSON.
- **Retries**: if a chunk's reply is malformed (invalid JSON or a schema mismatch), that chunk is retried once.
- **Errors**: an empty reply, or one cut off at the token limit (`finish_reason == "length"`), is an error straight away.
- **Reconciling the reply with code**:
  - results for step IDs that aren't in the chunk are dropped;
  - `consolidated_from_nodes` is trimmed to IDs inside the chunk;
  - after all chunks finish, every real step must have exactly one result.

  Any of these failures returns `502`.

**6. Enforcing the rules after the LLM** (`backend/app/context/consumers.py`, `apply_blueprint_context`). The model isn't trusted to follow the controls on its own:
- **Citations**: rule labels the model cited, or mentioned in its rationale, are collected, checked against the pack, and turned into **citations**.
- **Readable text**: bracketed labels in the rationale are replaced with readable codes.
- **Human-review controls**: if a step is governed by a control that requires human review:
  - an `automatable` verdict is **downgraded to `partial`**, and a note explaining why is added to the rationale;
  - the agent's `human_checkpoint` is raised to `review_before_action`.

**7. Persist and link**
- `repository.set_blueprint_overlay()` upserts the process's single blueprint overlay. It stores the per-step results, `baseline_version_id`, `generated_at` and `context_pack_id`; the pack ID is used later to flag the blueprint as stale when the context changes.
- `auto_link()` (Epic 29, US29.3) links the system names mentioned in the process to the application catalog. It links a name only when there is exactly one exact catalog match, and never changes a link a person set.

**8. Back in the UI**: the page:
- stores the overlay and clears the selected step;
- reloads that baseline's BPMN, so the canvas can colour each step by verdict;
- re-fetches the agent artifacts. They aren't deleted on regenerate, but whether they are stale (US12.4) may have changed.

From there the user can:
- override a step's verdict (a justification is required): `PATCH .../blueprint/nodes/{id}`;
- export the blueprint as Markdown;
- generate an agent artifact for a step: `POST .../blueprint/nodes/{id}/agent-artifact`. The artifact feeds the agent build, test and release lifecycle.

### The blueprint evaluation prompt

The prompt is built by `build_system_prompt` in `backend/app/blueprint/prompts.py` and sent from `_evaluate_chunk_once` as a single `system` message. One call is made per chunk:

| Call | `operation` | `response_format` | `max_tokens` |
|---|---|---|---|
| Blueprint evaluation (one per chunk) | `blueprint_evaluation` | `{"type": "json_object"}` | 16000 |

Below is the full prompt as sent when a Context Pack applies and the diagram is split into chunks. Parts of it change in other cases:
- **No organization context**: the `## Organization rules` paragraph, the `applicable_rules` field and the trailing `## Organization context` section are all left out.
- **Single chunk**: the `## The rest of the diagram` section is left out.

```text
You are evaluating a finalized as-is business process diagram for the Agentic Solution Generator project. For every step (node) listed below, decide whether an AI agent could perform it, and if so, propose what that agent would need.

## Step-type taxonomy -- classify every node into exactly one of these before scoring automatability
| step_type | description | default automation lean |
|---|---|---|
| data_retrieval_transformation | Look up, extract, reformat, calculate, or move data between systems | Strong yes |
| rule_based_decision | Branching governed by explicit, statable rules (thresholds, policy lookups) | Strong yes |
| document_generation | Producing a document/email/report from known inputs and a template or pattern | Strong yes |
| communication_notification | Sending a routine notification/status update to a known recipient | Strong yes |
| judgment_based_decision | Branching that depends on experience, negotiation, or unstated context an agent can't reliably access | Partial |
| exception_handling | Ad hoc problem-solving when something deviates from the normal path | Partial |
| approval_compliance_signoff | A step whose purpose is that a specific accountable human authorizes it | No |
| physical_manual_action | Requires physical presence/action in the real world | No |

## Automation decision rubric -- for every node, produce a verdict of "automatable", "partial", or "not_automatable", with a rationale grounded in:
1. The step-type classification above (a starting point, not the final word).
2. Whether the inputs the step needs are available as structured data or reliably extractable text/documents (information that only exists in someone's head or an unindexed system is evidence against "automatable").
3. Whether the step's decision logic can be stated as explicit rules/criteria (favor automation) vs. depends on unstated judgment, relationship context, or negotiation (favor "partial" or "not_automatable").
4. Whether getting the step wrong has a bounded, correctable cost (favor automation, especially with a human-review checkpoint) vs. an authority/liability cost only a specific accountable person can bear (favor "not_automatable" regardless of how mechanical the step looks).

## Organization rules -- rubric factor 4 is judged against the organization's own rules, listed in the Organization context section at the end. For every node, list in "applicable_rules" the labels of the rules that govern that step (an empty list when none do). A step governed by a control that requires human review must NOT be "automatable": use "partial" with a review_before_action checkpoint, or "not_automatable". Mention the rule's code in the rationale. Never invent rules that are not listed.

Never default to "automatable" purely because the step-type table leans that way -- ground the rationale in this specific node's actual label, predecessors/successors, and lane/actor context below, not just its type label.

## Agent spec -- every "automatable" or "partial" node needs a proposed agent_spec (name, purpose, trigger, required_inputs, expected_outputs, tools_systems_needed, human_checkpoint, consolidated_from_nodes). A "not_automatable" node gets agent_spec: null and a required, specific not_automatable_reason naming which rubric factor drove the decision -- never a generic "requires human involvement" with no reason stated.

## Grouping/consolidation -- recommend consolidating sequential nodes into one agent (same agent_spec, with consolidated_from_nodes listing every node id it covers) when they share the same actor/lane and have no human checkpoint between them in the diagram, and one node's output is consumed directly by the next with no branching decision a human currently makes in between. Keep nodes as separate agents when a human currently reviews/approves between them (preserve that checkpoint, don't optimize it away by default), or when they need fundamentally different tool/ system access. When you consolidate, still return ONE result entry per node_id (every node in the diagram must appear exactly once in your response) -- give each consolidated node the SAME agent_spec object, including the same consolidated_from_nodes list, rather than omitting any of the nodes it covers.

## Process nodes (JSON) -- you must return exactly one result for each node id below, no more, no fewer. Each node's predecessors/successors are given by their own label plus any flow condition, for context on what feeds into and out of this step:
[
  {
    "id": "<Task_...>",
    "label": "<step label>",
    "bpmn_type": "<userTask | serviceTask | exclusiveGateway | ...>",
    "actor_or_lane": "<lane name>",
    "predecessors": [{"label": "<previous step label>", "condition": null}],
    "successors": [{"label": "<next step label>", "condition": "<flow condition or null>"}]
  },
  ...one entry per step in this chunk
]

## The rest of the diagram -- context only. Do NOT return results for these nodes, and only consolidate among the nodes listed above:
[{"id": "<Task_...>", "label": "<step label>", "bpmn_type": "<type>", "actor_or_lane": "<lane name>"}, ...]

Respond with a single JSON object matching exactly this shape (no prose, no markdown code fences, just the JSON object):
{
  "nodes": [
    {
      "node_id": "<one of the ids listed above>",
      "verdict": "automatable" | "partial" | "not_automatable",
      "step_type": "data_retrieval_transformation" | "rule_based_decision" | "document_generation" | "communication_notification" | "judgment_based_decision" | "exception_handling" | "approval_compliance_signoff" | "physical_manual_action",
      "rationale": "string -- must reference this node's actual label/context, not just its type",
      "agent_spec": null | {
        "name": "string",
        "purpose": "string",
        "trigger": "string",
        "required_inputs": [{"name": "string", "source_or_destination": "string", "format": "string"}],
        "expected_outputs": [{"name": "string", "source_or_destination": "string", "format": "string"}],
        "tools_systems_needed": ["string"],
        "human_checkpoint": "none" | "review_before_action" | "review_after_action" | "escalation_on_exception",
        "consolidated_from_nodes": ["<node id>", "..."]
      },
      "not_automatable_reason": null | "string -- required and specific when verdict is not_automatable",
      "applicable_rules": ["<label from the Organization context section, e.g. C3>"]
    }
  ]
}

## Organization context
Rules, roles, and policy text that apply to this process. Each item has a label in brackets.
Where an item affects your answer, cite its label in the citation field asked for. In any text a
person will read (questions, rationales), refer to an item by its code or name -- never by its
bracketed label, which people cannot see. Do not invent rules that are not listed here.

[C1] <rule / control / role text>
[C2] <...>

Relevant policy passages:
[P1] (<clause ref>) <passage text>
```

## What happens when you push an agent to a registry ?

Publishing an agent pushes its **agent artifact definition** (the generated `AgentDefinition` JSON) into a registry and records that publication. No LLM is called.

**Before Publish is allowed**
- An agent goes through these lifecycle stages: `DRAFT → VALIDATED → SIMULATION_READY → SIMULATING → EVALUATED → RELEASE_CANDIDATE → APPROVED → DEPLOYED`. Only an **approved** agent version can be published.
- **Approvals:** an agent becomes approved once enough independent people approve it (`sod.approve_agent`). The number required comes from governance settings (`required_agent_approvals`, which can be overridden per business unit).
- **Approvals expire:** a high-risk change to the agent afterwards (for example, a definition edit) clears the approval and the release-candidate mark (`sod._invalidate`). The agent then has to be approved again before it can be published or republished.

**1. Frontend** (`frontend/src/components/PublishPanel.tsx`)
- The panel lists the tenant's registries (`GET /api/registries`). Unreachable ones are greyed out.
- It shows the twin confidence badge, which is evidence for the decision, never a gate.
- Clicking **Publish** calls `POST /api/processes/{id}/agent-artifacts/{artifact_id}/publish` with `{ "registry_name": ... }`.

**2. Endpoint checks** (`backend/app/api/publish.py`)
- **Permission:** the user needs `AGENT_PUBLISH` (`AgentPublishDep`).
- **Segregation of duties** (`sod.check_publisher`, Epic 37, US37.4):
  - if the agent version isn't approved yet, the request returns `409` ("Approve this agent version before publishing it");
  - if every current approver is the person publishing, it returns `403` ("You approved this agent version -- someone else must publish it"). The refusal is written to the audit log (`audit.record_refusal`).
- **Registry lookup:** the registry is looked up by name among the tenant's registries, which are built per request (`app/registry/manager.py`). There are three sources:
  - the tenant's own `registries` rows, set on the organization admin page (`/api/organization/registries`);
  - otherwise, for the first tenant only, the `REGISTRIES` environment variable;
  - otherwise the built-in `local` registry.

  An unknown registry name returns `404`.

**3. The push** (`repository.publish_agent_artifact`)
1. The artifact's stored definition is loaded and validated.
2. Its **tag** is taken from the blueprint: the `step_type` of the artifact's primary step (for example `rule_based_decision`).
3. `connector.push(agent_name, definition, tags, source_process_id, source_node_ids, pushed_by)` is called. Every registry type implements the same `RegistryConnector` interface (`app/registry/base.py`): `check_health`, `push`, `pull`, `search`.
   - Today the only connector type is **`local`** (`app/registry/local.py`). It inserts a `RegistryEntryModel` row (`regentry_...`) into the product's own Postgres, in its own short-lived session, and commits it.
   - A vendor registry would be a new class added to `CONNECTOR_TYPES`, with no other code changes.
4. **Only after the push succeeds**, an `AgentPublicationModel` row (`pub_...`) is written. It stores the registry name, the registry entry ID, the publisher, and `source_artifact_generated_at` (the generation timestamp of the artifact version that was pushed). The version number is the publication's position in this artifact's publication history (1, 2, 3, ...).
5. **Errors:** a connector error returns `502`, with different messages for "unreachable" (`RegistryUnreachableError`) and "rejected credentials" (`RegistryAuthError`). No publication row is written, so a failed publish never changes the tracked status (US15.5). The UI keeps showing the last known status next to a retryable error.

**4. Status afterwards** (`GET .../publish-status`)
- `lifecycle_status` is one of:
  - `generated`: never published;
  - `published`;
  - `deployed`: any publication was ever marked deployed.
- `needs_republish` is true when the artifact has been regenerated since its latest publication, i.e. when the stored `source_artifact_generated_at` no longer matches.
- The full publication history is listed, newest first.

**5. Marking it deployed**
The local registry has no deployment API to sync from, so deployment is recorded by hand: `POST .../publications/{publication_id}/mark-deployed` sets `status="deployed"`, `deployed_at` and `deployed_by`.

**Browsing the registry**
- `GET /api/registries/search?q=` searches every registry the tenant has, by agent name or tag. An empty query lists everything.
- There is also a lower-level `POST /api/registries/{name}/push`. It pushes a raw definition without approval checks or publication tracking (it still needs `AGENT_PUBLISH`). The Publish button doesn't use it.

## How do we test an agent when the actual tools are not available ?

Agents are tested in the **digital twin** (`backend/app/twin/`).
- The agent's own system prompt runs **for real** against the LLM.
- Every system it would call, and every person it would ask for approval, is replaced by a **simulated counterpart**.
- Nothing calls a live production system. The only real calls possible are to a registered non-production endpoint, in API mode.

A test is a **scenario** (`TwinScenario`). It contains:
- the agent's inputs;
- how each system should behave (system stubs);
- how the human reviewer should behave;
- optional faults and an environment;
- the expected steps and outputs to grade against.

**1. Start a run**: `POST /api/processes/{id}/scenarios/{scenario_id}/run` (`backend/app/api/twin.py`)
- The user needs `AGENT_TEST`.
- The agent must have reached **`SIMULATION_READY`** (`_require_simulation_ready`). If not, the request returns `400` and lists the checklist items still missing.
- A `SimulationRunModel` row is created in status `CREATED`, with a **snapshot** of every versioned input to the run (Epic 22), and the request returns `202`.
- The run itself executes as a background task (`repository._execute_twin_run`), under the business unit's model routing. It can be polled, cancelled and resumed.

**2. Turning each system name into a callable tool** (`_get_or_infer_tool_schemas`)
The blueprint only gives plain system names (for example `"ERP system"`). Each one becomes a callable tool schema from the first source that applies:
1. **Catalog OpenAPI spec** (Epic 29): if the system is linked to a catalog application with an OpenAPI spec, the real operations are used. These are rebuilt on every run, with no LLM call.
2. **Sample records** (Epic 36): if the scenario's environment has `system_data` for that system, a `lookup` / `create` / `update` tool is generated from the data (`records.py`), with no LLM call.
3. **LLM inference** (`schema_inference.py`): otherwise, one LLM call invents a plausible tool schema for every remaining system at once. The result is cached per artifact (`TwinToolSchemaModel`) and only re-inferred when the agent's list of systems changes.

**3. The agent loop** (`engine.run_scenario`)
- **Messages:** the agent's stored `system_prompt` is sent unchanged, followed by a separate **twin-instructions** system message (below) and the scenario inputs as the user message.
- **Human checkpoints:** if the agent has one, it is offered as one more tool, `request_human_decision`. Asking a person goes through the same tool-calling path as calling a system.
- **Each turn:** one model call (`operation="twin_run"`). For each tool call the model makes:
  1. **Policy gateway** (`gateway.check_tool_permission`, Epic 20): the call is checked against the agent's declared `tool_contracts` / `permissions`. A denied call returns `permission_denied` to the agent and is always recorded as a deviation. An agent that declares no contracts can call any tool offered to it.
  2. **Guardrails** (`GuardrailTracker`): limits on steps, tool calls, wall-clock time, model calls, tokens and cost, plus loop detection. Exceeding a limit ends the run as `ESCALATED` / `FAILED` / `TIMED_OUT`.
  3. **Fault injection** (Epic 21, `environments.py`): the scenario's own faults are tried first, then the environment's fault catalog, each with a seeded probability. Possible faults are `timeout`, `http_500`, `http_429`, `permission_denied`, `dependency_unavailable`, `invalid_response`, `missing_data`, `slow_response`, `duplicate_event` and `out_of_order_event`. A fault that fires replaces the normal response.
  4. **Resolve the response.** Static and API stubs take priority over sample records, and both take priority over Proxy mode:
     - **Static:** the scenario's match → response table. If no rule matches, it falls back to Proxy and records `static_fallback`.
     - **API:** a real HTTP call to the linked application's registered **non-production** endpoint. Before every call, `catalog/endpoints.check_endpoint` re-checks that the endpoint is not production.
     - **Sample records:** a deterministic lookup or write against the environment's records. Writes go to a per-run copy, so a later lookup in the same run sees them, and the environment itself is never changed.
     - **Proxy (default):** an LLM invents a plausible response for that specific call (`operation="twin_system_simulation"`).
  5. **Human checkpoint resolution** (`simulators.resolve_human_decision`):
     - `probability`: a seeded random approve or reject;
     - `rule`: one condition over the agent's proposed action, e.g. `amount gt 10000 → reject`;
     - `manual`: the run **suspends** in `WAITING_FOR_HUMAN`, and a person resumes it with `POST .../twin-runs/{run_id}/resume`.
- **End of the loop:** the loop ends when the model replies without tool calls. Its final JSON is the output. If the reply isn't valid JSON, one repair turn is allowed and recorded as a `RETRY` step.

**4. Grading**: no LLM judge is involved (`engine.grade_run`).
- **Trace:** the actual sequence of tool calls and checkpoints is compared with `expected_steps`, either exactly (`step_match="exact"`) or in order with extra steps allowed (`step_match="in_order"`).
- **Output:** the final output is compared field by field with `expected_outputs`.
- **Additional checks:**
  - compliance assertions from the situation (Epic 30: escalated, blocked, reviewed, logged);
  - the expected decision of a dataset row (Epic 32).
- **Result:** the run ends with `graded_passed` and a list of plain-English `deviations`.
- **Scores:** `evaluation.evaluate_run` then computes 12 evaluation dimensions from the trace (efficiency, latency, token use, cost and so on), measured against the agent's own guardrails.

### Digital twin prompts

The **twin-instructions** system message is built by `engine._twin_instructions` and appended after the agent's own `system_prompt`. It exists only for the test run. The lines in `<...>` appear only when they apply: the agent has a human checkpoint, the run is graded on a decision, or the next gateway is routed by the agent.

```text
Digital twin simulation instructions for this test run only (not part of your normal operating instructions above):
- Every system you have access to is available as a callable tool -- call the matching tool instead of describing what you would do.
<- When you need human review/approval as instructed above, call the "request_human_decision" tool instead of stopping -- you will get back a decision of "approve" or "reject" and should continue accordingly.>
- Once you are completely finished and have no more tool calls to make, respond with ONLY a JSON object whose keys are exactly: [<output field names>]. Do not include any other text.
<- Set "<decision field>" to how you decided the case: "approve", "reject", or "escalate" (you could not decide it yourself and passed it to someone else).>
<- Set "route" to exactly one of [<branch labels>]: the branch the next decision point ("<gateway label>") should take for this case, based on what you found.>
```

The **tool-schema inference** prompt (`schema_inference._build_system_prompt`) is sent as a `system` message with `operation="twin_tool_schema_inference"`, `json_object`, and `max_tokens=16000`. It is sent once per artifact, and the result is cached.

```text
You are designing a synthetic test double for a business-process automation agent, for testing purposes only -- nothing here will be called live.

The agent's purpose: <purpose>
Trigger: <trigger>

For EACH of the following systems/tools the agent needs, propose ONE plausible callable tool schema it would use to interact with that system: a function name, a short description, JSON-schema parameters (an object schema with "type": "object", "properties", and "required"), and a short description of what a typical successful response looks like (field names and what they represent). Invent something plausible for a system with this name -- don't ask for clarification.

Systems:
- <system name>
- <...>

Respond as JSON: {"tools": [{"system_name": <exactly one of the system names above>, "tool_name": str, "description": str, "parameters": <json schema object>, "response_shape_description": str}, ...]} -- exactly one entry per system listed above.
```

The **Proxy-mode system simulation** prompt (`simulators._fabricate_proxy_response`) is sent as a `system` message with `operation="twin_system_simulation"`, `json_object`, and `max_tokens=8000`. It is sent once per proxied tool call.

```text
You are simulating the system "<system name>" for testing purposes only -- this is not a real call. A tool named "<tool name>" (<tool description>) was just called with these arguments: <arguments JSON>

A typical successful response looks like: <response_shape_description>

Return ONLY a JSON object that is a plausible response to this specific call.
```

## How do we simulate the end-to-end flow without tools and test cases ?

Two features cover this:
- **Orchestration rehearsal** (Epic 16) runs the whole process with every agent together.
- **Generated test cases** (Epics 24/25, 32/33) create the cases to run, so nobody has to write them by hand.

The **process twin** (Epic 36) combines the two: it runs the rehearsal over many generated cases and compares the result with how the process runs today. Every system call inside it is simulated exactly as in the previous section.

### Rehearsing the whole process (`backend/app/twin/orchestrator.py`)

**Start**: `POST /api/processes/{id}/orchestration/scenarios/{scenario_id}/run` needs `AGENT_TEST`.
- It creates a `kind="orchestration"` run in status `CREATED` and returns `202`.
- The run executes as a background task (`_execute_orchestration_run`).
- Every task step that has a generated agent becomes a runnable node (`_build_node_artifacts`). Each of those agents must be `SIMULATION_READY`, and its tool schemas are resolved once per artifact, so a consolidated agent covering several steps is resolved only once.

**The walk** (`run_orchestration`) starts at the start event and follows the approved BPMN, step by step. Each node type is handled differently:

| Node | What happens |
|---|---|
| **Start event** | The scenario's `inputs` become the case data. |
| **Automatable task** | Its agent runs through `engine.run_scenario`: the same loop, policy gateway, faults, stubs and checkpoint simulation as a single-agent twin run. All its steps go into one trace for the whole run. |
| **Manual task** (no agent) | Simulated according to `manual_node_config`. In the default `human_checkpoint` mode, a seeded approve or reject (or `manual`, which suspends for a real person) is recorded as `human_decision`. In `fixed_stub` mode, a fixed output is returned. |
| **Exclusive / inclusive gateway** | The branch is chosen by the gateway's routing (below) and recorded as a `DECISION` step. |
| **Parallel gateway** | Not supported yet. The run fails with a clear deviation. |
| **End event** | The run completes, and the final output is the case data. |

**Gateway routing** is set per gateway (`gateway_routing`), with a scenario-wide default (`default_gateway_routing`):
- **`configured`** (the default): the scenario fixes the branch (`gateway_decisions[gateway_id].to_node_id`).
- **`rules`** (Epic 36, US36.3): the process's **confirmed decision rules** (Epic 33) are evaluated against the case data. The first matching rule by priority wins, and otherwise the default rule.
- **`agent`**: the agent just before the gateway is asked to set a `route` field to one of the branch labels. The value is matched to a branch ignoring case and punctuation.
- **Fallbacks:** if `rules` or `agent` can't decide, the configured decision is used. If there is none, the run fails with a deviation naming the branch options.

**Handing data from one step to the next**
- `handoff` sets what the next step receives:
  - `previous_output`: only the previous step's output;
  - `case_file`: the start inputs plus every output so far.
- `data_mapping_mode` (set per receiving node) reshapes that data into the next agent's `input_schema`:
  - `exact_field_contract`: the field names must match exactly, or the run fails;
  - `llm_adapter`: an LLM maps the fields and puts null for anything it can't derive (prompt below);
  - `auto`: fields with the same name are copied, and only the missing ones go to the LLM.

**Grading** (`grade_orchestration`): the steps the run actually visited are compared with `expected_path`, and the final output with `expected_final_output`. No LLM judge is involved. Runs can be cancelled, can suspend at any manual checkpoint, and are resumed with `POST .../orchestration/runs/{run_id}/resume`.

### Generating the test cases (`backend/app/twin/test_builder.py`, `backend/app/oracles/service.py`)

None of these require writing a scenario by hand.

- **Coverage targets** (`build_context`, deterministic, no LLM): read from the agent and the approved BPMN. They include:
  - a happy path;
  - every branch of the gateways right after the agent;
  - reviewer outcomes;
  - the organization rules and approval authorities that apply.
- **Suggested situations** (`POST .../test-builder/suggest`): an LLM proposes business-readable test situations (given / when / then) that cover targets not yet covered. Each one includes:
  - inputs;
  - per-system behaviour (`normal`, `down`, `timeout`, `denied`, `bad_data`, `no_record`, `custom_data`);
  - a reviewer;
  - expectations.

  Every name in the reply is checked against the real system, field and target names before use. A situation can also be written in the user's own words (`/describe`). Situations become twin scenarios: system behaviours turn into stubs and faults, and expectations turn into graded steps.
- **Test data** (`POST .../test-builder/data`): synthetic records in several modes:
  - `typical`, `edge`, `messy`;
  - `describe`: records matching the user's description;
  - `mutate`: variants of a seed record, each changing one thing;
  - `clone`: look-alikes of a pasted real example, with every identifying detail replaced;
  - `near_miss`: values just either side of screening and threshold rules.

  Value formats follow the organization's jurisdictions (for example, IBAN or postcode shapes).
- **Reviewer personas** (`/personas`): an LLM casts contrasting reviewers: always approves, always rejects, or rejects above a threshold. `/authority-personas` creates one reviewer per real approval-authority limit, with no LLM.
- **Labelled datasets and the rule oracle** (Epics 32/33):
  - `POST .../decision-rules/propose` drafts executable rules for each decision point. A person confirms them.
  - `.../oracle/label` labels dataset rows with the decision those rules give.
  - **Label-first generation** (`.../datasets/{id}/generate`) picks the decision first (approve / reject / escalate, in a chosen mix), asks the LLM for records that should produce it, and **keeps only the records the rule oracle agrees with**. The rest are discarded.
  - **Metamorphic variants** (`.../relations/{id}/variants`) perturb rows deterministically, with no LLM, so the batch can check that the decision changes, or stays the same, as expected.

### Running it at scale: the process twin (`backend/app/process_twin/`)

`POST /api/processes/{id}/process-twin/batches` runs one orchestration scenario over every row of a dataset.
- **One run per row:** each row becomes its own `kind="orchestration"` run. It carries the row's inputs, a pinned dataset version and a per-case seed, so any case can be replayed alone. Each run is queued as a job, and the batch can be paused, cancelled or stopped by a budget cap. `POST .../process-twin/estimate` predicts the cost first.
- **Efficiency report:** when the batch finishes, the report compares the **as-is** process with the **to-be** process for every case, using the same random draws on both sides:
  - **As-is:** people do every task, with waits and handling times from the process owner's operating model.
  - **To-be:** agents do the tasks in their measured time, plus reviewer time where an agent asked for review.
- **Failed cases:** a case the twin couldn't finish counts as fully manual in the to-be process, so a failing twin never inflates the savings.

### Orchestration prompts

The **data adapter** prompt (`orchestrator._adapt_data`, `llm_adapter` mode or the missing fields in `auto` mode) is sent as a `system` message with `operation="orchestration_data_adapter"`, `json_object`, and `max_tokens=8000`:

```text
Reshape the following JSON data to match the target field list exactly, for one step of a multi-agent test rehearsal. Use the source data's values where they clearly correspond; use null for any target field you cannot derive from the source data -- never fabricate a value.

Source data: <previous output or case file JSON>

Target fields: ['<name> (<format>, <source_or_destination>)', ...]

Return ONLY a JSON object with exactly these keys: [<target field names>].
```

The **situation suggestion** prompt (`test_builder.suggest_situations`, `operation="twin_builder_suggest"`). The sentence about organization rules is included only when rules apply. The `<context block>` lists the agent's name, purpose, trigger, steps, input and output fields, exact system names, checkpoint, success criteria, the decision points after it, the coverage target IDs, the organization rules, and the threshold-field note.

```text
You help a business process owner test an AI agent in a safe simulated copy of their process (a 'digital twin'). Nothing is called live.

<context block>
Already covered by existing tests: <covered target ids, or "(nothing yet)">
<The user especially wants to explore: <focus>>

Propose <count> distinct, realistic test situations, written for a non-technical business reader. Prioritise coverage targets not yet covered: start with one typical happy-path case, then decision-point branches, reviewer outcomes, a system being unavailable, and messy input data. <Include at least one situation designed to trigger each organization rule listed above, citing its coverage target id in covers and stating in expect.compliance what the rule demands (escalation to the named role, a blocked action, a review before acting, or a logged decision). >Use realistic, specific values (names, amounts, dates) -- invent them, never use real people. Only use the exact system names, field names, and coverage target ids given above.

Respond as JSON: {"situations": [{"title": str (short, vivid, business language), "given": str (the case and its data, one sentence), "when": str (what happens around it -- system conditions, timing; one sentence), "then": str (what a correct agent should do, one sentence), "covers": [coverage target ids from the list above], "inputs": {<input field name>: <realistic value>, ...}, "system_behaviour": {<exact system name>: one of "normal"|"down"|"timeout"|"denied"|"bad_data"|"no_record"|"custom_data"}, "system_data": {<exact system name>: {<field>: <value>}} (only for systems set to "custom_data" -- the record that system returns), "reviewer": {"behaviour": one of "none"|"approve"|"reject"|"threshold", "field": str|null, "threshold": number|null}, "expect": {"systems_used": [exact system names the agent should call, in order], "review_decision": "approve"|"reject"|null, "output_fields": [output field names that must be produced], "compliance": [{"control": <a rule coverage target id, e.g. "ctl:...">, "kind": "escalated"|"blocked"|"reviewed"|"logged", "role": str|null, "system": <exact system name>|null, "field": <output field name>|null}] (only for situations testing a rule -- what the rule demands)}}, ...]}
```

The **label-first generation** request (`oracles.generate_label_first`) reuses `test_builder.generate_records` in `describe` mode, in batches of up to 25 records. The user's description it passes in is:

```text
Every record must be a case these rules decide as '<approve | reject | escalate>':
- at '<gateway label>': if <rule> then branch '<branch label>' (case is <outcome>)
- <...>
<Make the formatting messy (typos, odd casing, extra spaces) without changing what the rules decide.>
Vary the records realistically; stay clearly inside the rule (not on a boundary).
```
