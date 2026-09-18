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
- **This applies to `<bpmn:lane>` elements too, not just flow nodes/flows.**
  A lane with a fully-populated `flowNodeRef` list but no matching
  `<bpmndi:BPMNShape bpmnElement="Lane_x">` is semantically valid BPMN but
  **will not render as a visible swimlane band at all** -- confirmed by
  reading bpmn-js's own import code (`BpmnTreeWalker.handleLane` only
  visits/draws a lane via `visitIfDi`, which is a no-op when the DI map has
  no entry for that lane's id). A multi-actor process with laneSets but no
  lane DI shapes silently degrades to a flat, ungrouped scatter of nodes in
  the rendered canvas -- this was a real defect (see the decision log's
  "swim lanes never rendered" entry), not a hypothetical one.

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

## Auto-layout (US3.5) -- swimlanes for a real multi-actor process

Default to a left-to-right flow direction, **and real swimlane bands, not
just a semantic grouping**. `app/bpmn/layout.py`:

- **X (time/sequence)**: rank via `_rank_nodes` (Kahn's algorithm --
  finalize a node's rank only once every predecessor already has one, so
  rank = max(predecessor ranks) + 1 falls out exactly, no revisiting). A
  node's actor doesn't affect its X position -- time flows left to right
  regardless of who's doing the work. **Real extracted processes can
  contain a genuine cycle** (a rework/retry loop, e.g. "if a check fails,
  escalate and re-confirm clearance") -- not malformed extraction output,
  just something a strict left-to-right rank can't represent for the nodes
  actually inside the loop. Kahn's algorithm never finalizes a node whose
  predecessor chain doesn't bottom out in already-ranked nodes, which is
  exactly true of every node in (or only reachable via) a cycle -- those
  get a deterministic fallback rank via a plain single-visit BFS instead.
  **Do not** go back to a relaxation-based rank (re-queue a node whenever a
  *longer* path to it is found) -- that's the textbook DAG approach but
  never terminates on a cycle, and a real document hung the single
  -threaded API process indefinitely with exactly this (see the decision
  log). Kahn's algorithm's "never revisit a finalized node" property is
  what guarantees termination either way.
- **Y (actor/lane)**: each lane (including a catch-all band for elements
  with no `actor_id`) gets a **fixed, non-overlapping vertical band**, in
  lane declaration order, sized for the max number of that lane's nodes
  landing on the same rank (usually 1). A node's Y comes from its own
  lane's band + its row within that (lane, rank) pair -- **never** from a
  row index computed within the rank alone. That was a real bug: two nodes
  in *different* lanes at *different* ranks can each be "row 0 in their
  rank," which produced the same Y for unrelated nodes and "row 1" for
  whichever lane happened to have 2 members at some rank, regardless of
  which lane it actually was -- the diagram looked like one undifferentiated
  chain of boxes for any process with more than a couple of actors,
  because nothing kept a given lane's nodes at a consistent height across
  ranks *or* gave the lanes themselves a DI shape (see above).
- **Lane DI shapes**: `compute_lane_bounds(model, node_layout)` computes
  each lane's own bounds as the tight enclosing rectangle of its current
  member nodes (padding: `_LANE_PADDING`), with every lane's X-extent
  normalized to the full diagram width (`_LANE_X_MARGIN` overhang) so the
  bands read as one shared swimlane structure. Always derived from the
  nodes' *actual current* positions (including Epic 5 chat-apply's
  preserved/dragged ones), not recomputed from a theoretical rank/row
  formula -- so a lane's band always tightly fits what's really in it,
  including after manual edits. An empty lane (no elements currently
  assigned) still gets a thin placeholder band, stacked after the
  populated ones, rather than vanishing from the diagram.
- `isHorizontal="true"` on each lane's `BPMNShape` is the DI convention for
  "stacked top-to-bottom, label rotated along the left edge" -- bpmn-js
  actually defaults to `true` when this attribute is absent, but set it
  explicitly for spec-conformance and other tools' compatibility, not
  because bpmn-js strictly requires it.
- **Edge routing**: `compute_edge_waypoints` -- never a straight line from
  source-center to target-center. That reads fine in a tiny same-lane
  example but cuts diagonally across every lane band and unrelated node in
  between the moment source/target are in different lanes, which is the
  common case in any real multi-actor process (found live: reported as
  "wiring is messy, crossing over each other"). Three cases: same-lane
  forward is a single straight horizontal segment; different-lane forward
  is orthogonal (right, then vertical at the midpoint of the *gap* between
  the two rank columns -- never on top of a node column -- then left into
  the target); backward (`target.left < source.right`, covering both a
  genuine rework loop and any same-rank edge) routes as a loop below the
  *entire* diagram's lowest point, not just these two nodes, matching how
  real BPMN tools draw a loop-back. A straight backward line would run
  through everything between source and target.

This does not need to be a full graph-layout library -- correctness (no
overlapping shapes within a lane, edges routed sensibly, lanes that
actually render as lanes) matters more than optimality. A newly auto-placed
node can still visually overlap a *preserved* one from a prior draft if the
graph shape changed a lot (Epic 5's "preserve positions" chat-edit path) --
accepted, with "Refresh Layout" (a full relayout with no preserved
positions) as the user-facing escape hatch.

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
      versa -- **including lanes** (a lane with no DI shape silently fails
      to render as a swimlane, see "ID conventions" above).
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
