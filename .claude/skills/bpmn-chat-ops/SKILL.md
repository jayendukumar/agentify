---
name: bpmn-chat-ops
description: Use when translating a user's natural-language chat instruction into a diff/patch against the current BPMN diagram (add, amend, or delete a node or relationship) in the Agentic Solution Generator project. Covers intent classification, the diff schema, disambiguation, and the confirm-before-apply pattern.
---

# BPMN Chat-Driven Editing

This skill governs the chat interface's translation of a user message into a
diagram change (planning epic: `planning/epics/05-chat-editing.md`). It
builds directly on the `bpmn-authoring` skill's ID conventions and
validation checklist -- every diff produced here must still pass that
validation before being applied.

## Hard rule: never apply silently

Every chat-driven edit MUST go through: **parse intent -> build diff ->
present diff to user -> user confirms -> apply diff -> validate result ->
log to audit trail.** Never mutate the live diagram directly from a parsed
instruction without the confirmation step (Epic 5, US5.3) -- this applies
even when the instruction seems unambiguous. The cost of a wrong silent edit
(user loses trust in the tool) is much higher than the cost of one extra
confirmation click.

## Intent taxonomy

Classify every chat instruction into one of:

| Intent | Example phrasing | Diff shape |
|---|---|---|
| `add_node` | "add a review step after the approval task" | insert new element + new flow(s) connecting it in |
| `delete_node` | "remove the manual data entry step" | remove element + reconnect its predecessor(s) directly to its successor(s) |
| `rename_node` | "rename this to 'Manager Approval'" | update element `label` only |
| `reassign_actor` | "this should be done by Finance, not Ops" | update element `actor_id`, move to a different lane/pool |
| `change_type` | "this should be a decision, not a task" | change element `type`, may require adding/removing gateway + flows |
| `add_flow` | "add a path from step 3 straight to the end if rejected" | new `flow` between two existing elements, optionally with a `condition` |
| `delete_flow` | "remove the connection between these two steps" | remove a `flow`; validate the diagram doesn't end up with an orphaned node afterward |
| `reroute_flow` | "this should go to step 5 instead of step 4" | change a flow's `to` (or `from`) target |
| `explain` (not an edit) | "what does this step do" / "who owns this task" | no diff -- answer from the element's `source_refs`/metadata, see `process-doc-ingestion` schema |

If an instruction doesn't map cleanly to one of these, do not force it into
the nearest category -- ask a clarifying question instead (see below).

## Diff schema

Represent every proposed change as a structured diff, not a full
diagram replacement -- this is what gets shown to the user as a preview and
written to the audit log:

```json
{
  "intent": "add_node|delete_node|rename_node|reassign_actor|change_type|add_flow|delete_flow|reroute_flow",
  "summary": "human-readable one-line description of the change",
  "target_element_ids": ["el-3"],
  "operations": [
    {"op": "add_element", "element": { "...": "canonical schema shape" }},
    {"op": "remove_element", "element_id": "el-3"},
    {"op": "update_element", "element_id": "el-3", "fields": {"label": "Manager Approval"}},
    {"op": "add_flow", "flow": {"from": "el-2", "to": "el-3", "condition": null}},
    {"op": "remove_flow", "flow_id": "f-4"},
    {"op": "update_flow", "flow_id": "f-4", "fields": {"to": "el-5"}}
  ]
}
```

Element/flow IDs referenced here must match the stable IDs from the
`bpmn-authoring` skill's ID convention -- the diff operates on the same IDs
that appear in the rendered BPMN, so the UI can highlight exactly what's
about to change.

## Disambiguation rules (US5.4)

Ask a clarifying question (rather than guessing) when:
- The instruction refers to an element by a description that matches more
  than one node (e.g. "the review step" when there are two review steps) --
  list the candidates and ask which one.
- The instruction implies a structural change whose shape is genuinely
  ambiguous (e.g. "add an approval step" without saying where -- ask where
  in the sequence, unless there's an unambiguous single insertion point).
- The instruction would delete a node that has multiple outgoing branches
  with different conditions -- ask how the remaining flows should be
  reconnected rather than guessing a default.

Do not ask a clarifying question when the instruction is merely informally
phrased but has only one sensible interpretation -- over-asking erodes trust
as much as guessing wrong (see Epic 5 notes on excessive back-and-forth).

## Audit trail entry (US5.5)

Every applied diff must be logged with: timestamp, user, the diff object
above, the diagram version before and after, and the original chat message
text that produced it. This is what powers Epic 5's audit trail and Epic
6's version history/diff view.
