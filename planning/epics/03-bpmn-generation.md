# Epic 3 -- As-Is BPMN Generation Engine

**Status: all 7 stories implemented** (`backend/app/bpmn/`). Verified live against real Epic 1/2-extracted data (an actual 2-step process from a real document upload) and across a backend restart.

**Architecture note that changes how to read this epic:** US3.1 ("LLM
extraction of process structure") is already satisfied by Epic 1/2 -- the
`ProcessSchema` this epic reads already has actors/elements/flows/
confidence from `app/ingestion/structuring.py`. Turning that
already-structured schema into BPMN XML (US3.2-3.6) is a **pure,
deterministic transformation with no new LLM call** -- faster, free, and
fully unit-testable without mocking an LLM. See `app/bpmn/builder.py`'s
module docstring.

Goal: Turn extracted, structured process information into a valid
BPMN 2.0 XML diagram (including Diagram Interchange / layout), forming the
as-is baseline shown to users.

Depends on: Epic 1 (ingested content), Epic 2 (knowledge store).
Feeds: Epic 4 (diagram UI renders the output), Epic 6 (finalization).

## User Stories

US3.1 -- LLM extraction of process structure. **Already implemented (Epic 1/2).**
As a Process Analyst, I want the system to identify process steps, actors,
decision points, and their order from ingested content, so that I do not
have to manually enumerate the process before a diagram can be produced.
See the architecture note above -- nothing new to build here for this epic.

US3.2 -- Mapping to BPMN 2.0 constructs. **Implemented.**
As a Platform Engineer, I want extracted elements mapped to the correct
BPMN constructs (tasks, exclusive/parallel/inclusive gateways, start/end/
intermediate events, pools/lanes for actors, message flows for
system/actor handoffs), so that the output is semantically correct BPMN, not
just a generic flowchart.
`app/bpmn/mapping.py`. Task type follows actor type (`userTask` for a
`role` actor, `serviceTask` for a `system` actor, plain `task` if
unattributed). Gateway type is inferred from outgoing flow conditions
(all conditioned -> exclusive, none -> parallel, mixed -> inclusive).
**Known gap:** pools/message-flows for `external_party` actors aren't
implemented -- every actor (any type) gets a lane in one pool. See "Known
gaps" below.

US3.3 -- Valid BPMN 2.0 XML generation with layout. **Implemented.**
As a Process Analyst, I want the system to output valid BPMN 2.0 XML
including Diagram Interchange (DI) information, so that the diagram opens
correctly and is laid out in a readable form in the UI and in any standard
BPMN tool.
`app/bpmn/builder.py` -- hand-built via `xml.etree.ElementTree` (same
approach already used for Epic 1's Visio extractor and Epic 6's version
diff), matching the bpmn-authoring skill's exact skeleton and ID
conventions. Persisted in the `bpmn_drafts` table (Epic 2's DB layer,
extended for this epic -- see `app/db/models.py`'s module docstring).

US3.4 -- BPMN schema validation. **Implemented (structural checklist, not full XSD).**
As a Platform Engineer, I want generated BPMN XML validated against the
BPMN 2.0 XSD before being shown to the user, so that structurally invalid
diagrams are caught and either auto-corrected or flagged rather than
breaking the UI.
`app/bpmn/validation.py` implements the bpmn-authoring skill's explicit
checklist (well-formedness, dangling refs, orphan nodes, DI consistency,
duplicate ids) as code, not full OMG XSD validation (vendoring and wiring
the real multi-file BPMN20/Semantic/BPMNDI/DI/DC XSD bundle is real
additional scope, deliberately deferred). **Design decision worth knowing:**
`POST /generate` does NOT block on validation issues -- a generated draft
comes from real (possibly imperfect) extracted/merged data, and withholding
the whole diagram because one node is disconnected is worse than showing
it with the issue surfaced. Issues ride along in the response
(`BPMNDocument.validation_issues`). `PUT /bpmn` (manual edit) is stricter
and rejects invalid XML outright (400) -- human-authored input gets
immediate correction feedback instead. Finalizing (Epic 6) also rejects
invalid XML outright, for the same reason as PUT.

US3.5 -- Auto-layout for readability. **Implemented.**
As a Process Analyst, I want the generated diagram automatically arranged
(left-to-right or top-to-bottom flow, minimal crossing lines), so that it is
readable without manual repositioning of every element.
`app/bpmn/layout.py` -- rank-based BFS from start event(s), left-to-right,
grouped/stacked by lane within a rank, exactly the heuristic the skill
specifies. Not a full graph-layout library; correctness over optimality.

US3.6 -- Confidence flags on generated elements. **Implemented.**
As a Process Analyst, I want elements the system is unsure about (e.g.,
ambiguous branching, an actor that could not be clearly identified) flagged
for review, so that I know where to focus my review before finalizing.
Direct mapping: any source schema element with `confidence != "high"`
(already captured by Epic 1's extraction) lands in
`BPMNDocument.low_confidence_element_ids`, keyed by the *source schema*
element id (not the BPMN node id) so the UI can cross-reference back to
`ProcessSchemaView` (already built, Epic 1).

US3.7 -- Re-run generation with a different document set. **Implemented (full regeneration only).**
As a Process Analyst, I want to add/remove source documents and regenerate
the as-is diagram, so that I can refine the baseline without starting over
from a blank canvas manually.
`POST /generate` always regenerates from the process's current, fully
merged schema. `BPMNGenerateRequest.document_ids` (a filter to a subset of
documents) is accepted by the request schema but **not applied** -- once
documents are merged (Epic 1, US1.8), their individual contributions
aren't cleanly separable, so "regenerate from a subset" isn't a coherent
operation with the current merge design. Re-running ingestion with a
narrower document set is the only way to achieve that today.

## Notes / Open Questions

See the bpmn-authoring skill for BPMN XML structure conventions,
namespaces, ID conventions, and the element-mapping table this epic
followed -- now annotated with pointers to the actual implementation.

US3.6/US3.7 interact with Epic 5 (chat editing) -- chat editing is the
expected mechanism for a user to resolve a low-confidence flag or a
`validation_issues` entry, not just view it. Neither is built yet.

**Known gaps to revisit, not blocking:**
- Pools/message-flows for `external_party` actors -- everyone gets a lane in one pool for now (`app/bpmn/mapping.py`'s docstring).
- Intermediate events always map to `intermediateCatchEvent` -- the schema doesn't yet distinguish catch vs. throw semantics.
- Gateway fork/join pairing isn't synthesized -- a parallel gateway's branches reconverging at a shared downstream node is accepted as an implicit join rather than requiring an explicit converging gateway shape. See `app/bpmn/mapping.py`'s docstring for the reasoning.
- No full BPMN 2.0 XSD validation (see US3.4 above).
- `document_ids` filtering on regenerate is accepted but unused (US3.7 above).
