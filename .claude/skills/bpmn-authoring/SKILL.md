---
name: bpmn-authoring
description: Use when generating, editing, or validating BPMN 2.0 XML in the Agentic Solution Generator project. Covers namespaces, ID conventions, mapping from the project's extracted process schema to BPMN constructs, Diagram Interchange (DI) layout, and validation.
---

# BPMN 2.0 Authoring

This skill governs how the extracted process schema (see the
`process-doc-ingestion` skill) becomes valid BPMN 2.0 XML (planning epic:
`planning/epics/03-bpmn-generation.md`), and how BPMN is safely re-edited
by the chat-ops flow (`bpmn-chat-ops` skill).

**Status: implemented** -- `backend/app/bpmn/` (`mapping.py`, `builder.py`,
`layout.py`, `validation.py`). Generation is a pure, deterministic
transform of the already-extracted `ProcessSchema` -- no LLM call (see
`builder.py`'s module docstring). This document remains the reference for
maintaining/extending that code.

## Document skeleton

Every generated diagram must be a complete `<bpmn:definitions>` document with
both the semantic model and its Diagram Interchange (DI) -- never emit BPMN
without DI, or the diagram will not render meaningfully in the UI canvas.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions
    xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
    xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
    xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
    xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
    id="Definitions_<process-id>"
    targetNamespace="http://agentic-solution-generator/bpmn">
  <bpmn:process id="Process_<process-id>" isExecutable="false">
    <!-- flow elements -->
  </bpmn:process>
  <bpmndi:BPMNDiagram id="Diagram_<process-id>">
    <bpmndi:BPMNPlane id="Plane_<process-id>" bpmnElement="Process_<process-id>">
      <!-- BPMNShape / BPMNEdge per element -->
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
```

`isExecutable="false"` is intentional -- this is an as-is documentation
diagram, not an executable process definition, unless a later phase decides
otherwise.

## ID conventions

- Stable, human-traceable IDs: `Task_<slug>`, `Gateway_<slug>`,
  `Event_<slug>`, `Flow_<slug>`, `Lane_<slug>`. Derive `<slug>` from the
  source schema element's `id` (e.g. `el-1` -> `Task_el-1`), never regenerate
  random IDs on each pass -- stable IDs are required for chat-ops diffs
  (`bpmn-chat-ops` skill) and for version diffing (Epic 6, US6.4) to work.
- Every element referenced by DI (`BPMNShape`/`BPMNEdge` `bpmnElement`
  attribute) must exist in the semantic model, and vice versa. A dangling DI
  reference or a semantic element with no DI shape is a validation failure.

## Mapping: process schema -> BPMN constructs

| Source schema element | BPMN construct |
|---|---|
| `element.type == "start_event"` | `<bpmn:startEvent>` |
| `element.type == "end_event"` | `<bpmn:endEvent>` |
| `element.type == "intermediate_event"` | `<bpmn:intermediateCatchEvent>` or `<bpmn:intermediateThrowEvent>` (catch if waiting on something, throw if signaling something) |
| `element.type == "task"` | `<bpmn:task>` (or a more specific type -- `userTask` if a human actor performs it, `serviceTask` only if already known to be system-automated -- default to plain `task` when unsure) |
| `element.type == "decision"` with one condition per outgoing flow, mutually exclusive | `<bpmn:exclusiveGateway>` |
| `element.type == "decision"` with all branches always taken | `<bpmn:parallelGateway>` |
| `element.type == "decision"` with conditional, non-exclusive branches | `<bpmn:inclusiveGateway>` |
| `actor.type == "role"` | `<bpmn:lane>` within a `<bpmn:laneSet>` |
| `actor.type == "external_party"` or a distinct `system` actor exchanging messages | separate `<bpmn:pool>` with a `<bpmn:messageFlow>` to/from the main pool |
| `flow` between two elements in the same actor/lane | `<bpmn:sequenceFlow>` |
| `flow` crossing pools (different organizations/systems) | `<bpmn:messageFlow>`, not `sequenceFlow` -- sequence flows cannot cross pool boundaries |

Gateways must be paired correctly: every fork (diverging gateway) that
represents parallel execution needs a matching join (converging gateway) of
the same type before the flow returns to a single path. Do not leave forked
parallel branches unmerged unless they genuinely each end in their own end
event.

**Known gap in the current implementation:** join gateways are not
synthesized -- if a parallel gateway's branches reconverge on a shared
downstream node in the extracted schema's own flows, that node is accepted
as an implicit join rather than the builder inserting an explicit
converging gateway shape. See `app/bpmn/mapping.py`'s module docstring
before changing this; it's a deliberate scope decision, not an oversight.
Also: pools/message-flows for `external_party` actors aren't implemented
-- every actor gets a lane in one pool regardless of type.

## Auto-layout (US3.5)

Default to a left-to-right flow direction. Simple heuristic: assign each
element a rank via topological sort/BFS from the start event(s), place ranks
left-to-right (~180px horizontal spacing), and stack elements within the
same rank vertically (~120px spacing) grouped by lane. This does not need to
be a full graph-layout library initially -- correctness (no overlapping
shapes, edges routed sensibly) matters more than optimality.

## Validation checklist (US3.4)

Before returning generated or edited BPMN to the UI, verify:
- [ ] Well-formed XML, valid against the BPMN 2.0 XSD.
- [ ] Every `sequenceFlow`/`messageFlow` `sourceRef`/`targetRef` points to an
      element that exists.
- [ ] Every flow node other than a start event has at least one incoming
      flow; every flow node other than an end event has at least one
      outgoing flow (no orphans/dead ends).
- [ ] Gateway fork/join pairing is consistent (see above).
- [ ] Every DI shape/edge references an existing semantic element and vice
      versa.
- [ ] No duplicate IDs.

On validation failure, do not silently emit the invalid diagram -- either
auto-correct the specific issue (e.g. add a missing join gateway) or return
a structured error identifying which check failed and on which element, so
the caller (generation pipeline or chat-ops edit) can react appropriately.

**Implemented as a structural checklist, not full BPMN 2.0 XSD validation**
(`app/bpmn/validation.py`) -- see this epic's planning doc for why vendoring
the real OMG XSD bundle was deliberately deferred. **Also implemented, and
worth knowing before changing either caller:** the checklist above is
*informational, not blocking* for `POST /generate` (a generated draft comes
from real, possibly-imperfect extracted data -- withholding the whole
diagram over one disconnected node is worse than surfacing the issue) but
*is* blocking for `PUT /bpmn` (a manual edit) and for finalizing (Epic 6) --
both are human-authored-or-approved actions that should fail fast with
clear feedback rather than silently accept something broken.
