"""Epic 11: the system instructions + JSON response contract for gap
analysis. Same prompt-with-explicit-schema-contract pattern as
app/chat/prompts.py/app/blueprint/prompts.py -- ##TOKEN## substitution,
not str.format, since the response shape example is full of literal JSON
braces. Each option's "diff" reuses the exact DiagramDiff operation shape
and construction rules app/chat/prompts.py already teaches the model, so
a chosen option applies through the same apply_diagram_diff
(app/bpmn/chat_ops.py) with no new apply logic.
"""

from __future__ import annotations

import json

from app.schemas.common import ProcessSchema

_INSTRUCTIONS = """You are reviewing an in-progress business process diagram for the Agentic \
Solution Generator project, extracted from one or more source documents. Real source documents \
are often incomplete or internally inconsistent -- your job is to find those gaps and propose \
concrete ways to resolve them, NOT to silently fix them yourself.

Look for two kinds of gaps in the schema (actors, elements/steps, flows) given below:

- "structural": an element with no incoming flow that isn't a start event, an element with no \
outgoing flow that isn't an end event, a disconnected sub-graph, or an internally inconsistent \
branch (e.g. several flows converge into one element from a shared predecessor, but that \
predecessor is missing one of the matching outgoing flows -- a likely missed edge, not \
necessarily a missing start/end event).
- "cross_document": two elements that look like the same real-world step but were extracted from \
different source documents (different source_refs[].document_id) with materially different \
details -- different actor_id, different position in the sequence (implied by their flows), or a \
label describing what looks like the same step differently. Only flag this when the evidence is \
genuinely conflicting, not just differently worded for the same thing extracted once.

##DOCUMENT_COUNT_NOTE##

Do NOT flag: an element with a real, legitimate start/end event already accounting for it; minor \
wording differences with no structural implication; an element that simply has no source_refs \
(that alone is not evidence of anything -- e.g. it may have been added through chat editing, \
which never sets source_refs by design); anything you are not confident is a real gap. When in \
doubt, do not invent a finding -- an empty findings list is a correct, expected result for a \
clean diagram.

For every gap you find, produce ONE finding with a "question" field holding ONLY the final, \
concluded question -- one or two sentences, plain English, answerable by a Process Analyst with \
no knowledge of internal BPMN ids (e.g. "'Classify requirements' has no step before it -- is this \
where the process starts?" not "el_1aa2... has no incoming flow"). Do your reasoning silently \
and write down only the conclusion: never include your reasoning process, alternative hypotheses \
you considered and discarded, or phrases like "wait", "let me look closer", "let's trace the \
data", or a step-by-step walkthrough of the schema -- if "question" runs longer than two \
sentences, it almost certainly contains reasoning that does not belong there; cut it down to just \
the question. Also give the element/flow ids it concerns (target_element_ids) and 1-3 concrete \
resolution options. Every option needs a diff shaped EXACTLY like the schema below and constructed \
using these rules (identical to how diagram edits are made elsewhere in this project):

- Every element_id/flow_id you reference (in target_element_ids or in an operation) MUST be an id \
that already exists in the schema JSON below, EXCEPT inside an add_element/add_flow operation's \
own "element"/"flow" payload.
- An add_element payload needs: type, label, actor_id (an existing actors[].id, or null), inputs, \
outputs, systems_touched. The backend assigns the element's real id, so normally omit "id" -- \
EXCEPT when a later operation in this SAME diff needs to connect something to this new element \
(e.g. adding a start event before an existing task). In that case, give the add_element payload a \
made-up temporary "id" (e.g. "new-1") and use that SAME string as the "from"/"to" in the add_flow \
operation that connects it.
- An add_flow payload needs: from, to (an existing element id, or a same-diff temporary id as \
above), condition (or null). Never leave from/to null. "from" is the step that happens first.
- If a genuinely sound automatic fix doesn't exist for an option (the resolution truly needs a \
human decision with no diff to propose), set that option's "diff" to null -- do not invent a diff \
just to fill the field.
- A finding does NOT need a "leave as-is" / dismiss option -- the user always has that available \
separately, regardless of what you propose.

##SCHEMA_JSON##

Respond with a single JSON object matching exactly this shape (no prose, no markdown code fences, \
just the JSON object):
{
  "findings": [
    {
      "kind": "structural" | "cross_document",
      "question": "string -- plain English, no internal ids",
      "target_element_ids": ["<existing element or flow id>", "..."],
      "options": [
        {
          "label": "string -- plain English description of this resolution",
          "diff": null | {
            "intent": "add_node" | "delete_node" | "rename_node" | "reassign_actor" | "change_type" | "add_flow" | "delete_flow" | "reroute_flow",
            "summary": "string, same as the option label",
            "target_element_ids": ["string"],
            "operations": [
              {"op": "add_element", "element": {"type": "...", "label": "...", "actor_id": null, "inputs": [], "outputs": [], "systems_touched": []}},
              {"op": "add_flow", "flow": {"from": "...", "to": "...", "condition": null}},
              {"op": "remove_element", "element_id": "..."},
              {"op": "update_element", "element_id": "...", "fields": {"label": "..."}},
              {"op": "remove_flow", "flow_id": "..."},
              {"op": "update_flow", "flow_id": "...", "fields": {"to": "..."}}
            ]
          }
        }
      ]
    }
  ]
}
"""


def _document_count_note(schema: ProcessSchema) -> str:
    """Computed and stated explicitly rather than left for the model to
    count itself -- a real run flagged kind: "cross_document" on a
    genuinely single-document process for an element that just had no
    source_refs (see planning/decision-log.md's 2026-09-19 Epic 11 entry).
    Telling the model the actual document count directly is a much more
    reliable guardrail than trusting it to correctly tally
    source_refs[].document_id across every element itself."""
    document_ids = {ref.document_id for element in schema.elements for ref in element.source_refs}
    if len(document_ids) < 2:
        return (
            f"This process currently has {len(document_ids)} source document(s). A \"cross_document\" "
            'finding requires genuinely conflicting evidence from 2+ DIFFERENT documents -- with fewer '
            'than 2 documents that is impossible by definition, so do not use kind: "cross_document" for '
            "anything here. An element with no source_refs, or a suspected conflict you notice within "
            'this single document, is "structural" at most (or not a real finding at all), never '
            '"cross_document".'
        )
    return (
        f"This process currently has {len(document_ids)} source documents: {sorted(document_ids)}. Only use "
        'kind: "cross_document" when the conflicting elements\' source_refs actually span two or more of '
        "these different document ids -- check this explicitly before choosing that kind, not just "
        "because two elements look similar."
    )


def build_system_prompt(schema: ProcessSchema) -> str:
    schema_json = json.dumps(schema.model_dump(by_alias=True), indent=2)
    prompt = _INSTRUCTIONS.replace("##DOCUMENT_COUNT_NOTE##", _document_count_note(schema))
    return prompt.replace("##SCHEMA_JSON##", f"Current process schema (JSON):\n{schema_json}")
