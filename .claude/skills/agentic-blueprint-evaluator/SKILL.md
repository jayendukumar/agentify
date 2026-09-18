---
name: agentic-blueprint-evaluator
description: Use when evaluating finalized BPMN process nodes for AI-agent automation feasibility and producing the agentic blueprint overlay in the Agentic Solution Generator project. Covers the step-type taxonomy, the automation decision rubric, the agent specification schema, grouping/consolidation heuristics, and non-automatable flagging.
---

# Agentic Blueprint Evaluation

This skill governs how a finalized BPMN diagram (see `bpmn-authoring`) is
evaluated node-by-node to produce the agentic blueprint overlay (planning
epic: `planning/epics/07-agentic-blueprint-engine.md` and
`planning/epics/08-blueprint-visualization.md`).

**Status: implemented (Epic 7 engine only -- Epic 8 visualization is still
frontend-only work)** -- `app/bpmn/nodes.py` parses a finalized version's
XML directly (not the live process schema, which can have drifted -- see
`VersionModel`'s docstring) into per-node context; `app/blueprint/`
(`prompts.py`, `service.py`) builds the one-call-per-diagram prompt from
that context and reconciles the LLM's response against the real node ids
(never trusting the response's node_id list, same principle as
`app/ingestion/structuring.py`); `app/api/blueprint.py` +
`app/db/repository.py`'s `set_blueprint_overlay`/`get_blueprint_overlay`/
`update_blueprint_node` persist the overlay as one JSON blob per process
(`BlueprintOverlayModel`), replaced in place on regenerate (US7.7). This
document remains the reference for maintaining/extending that code; see
`planning/decision-log.md`'s Epic 7 section for real defects/design
trade-offs found building it.

## Step-type taxonomy (US7.2)

Classify every node into exactly one primary type before scoring
automatability -- the type drives the default verdict below:

| Step type | Description | Default automation lean |
|---|---|---|
| `data_retrieval_transformation` | Look up, extract, reformat, calculate, or move data between systems | Strong yes |
| `rule_based_decision` | Branching governed by explicit, statable rules (thresholds, policy lookups) | Strong yes |
| `document_generation` | Producing a document/email/report from known inputs and a template or pattern | Strong yes |
| `communication_notification` | Sending a routine notification/status update to a known recipient | Strong yes |
| `judgment_based_decision` | Branching that depends on experience, negotiation, or unstated context an agent can't reliably access | Partial -- often "agent drafts recommendation, human decides" |
| `exception_handling` | Ad hoc problem-solving when something deviates from the normal path | Partial -- depends on how bounded the exception space is |
| `approval_compliance_signoff` | A step whose purpose is that a specific accountable human authorizes it | No -- automating removes the accountability the step exists for |
| `physical_manual_action` | Requires physical presence/action in the real world | No |

## Automation decision rubric (US7.1)

For each node, produce a verdict of `automatable`, `partial`, or
`not_automatable`, always with a stated rationale grounded in:
1. The step-type classification above (starting point, not the final word).
2. Whether the inputs the step needs are available as structured data or
   reliably extractable text/documents (if the step needs information that
   only exists in someone's head or an unindexed system, that's evidence
   against `automatable`).
3. Whether the step's decision logic can be stated as explicit rules/
   criteria (favor automation) vs. depends on unstated judgment, relationship
   context, or negotiation (favor `partial` or `not_automatable`).
4. Whether getting the step wrong has a bounded, correctable cost (favor
   automation, especially with a human-review checkpoint) vs. an
   authority/liability cost that only a specific accountable person can bear
   (favor `not_automatable` regardless of how mechanical the step looks).

Never default to `automatable` purely because a step-type table entry leans
that way -- the rationale must reference the specific node's actual inputs,
outputs, and context from the diagram, not just its type label.

## Agent specification schema (US7.3)

Every `automatable` or `partial` node gets a proposed agent spec. `node_id`
is the node's actual BPMN element id from the finalized version's XML
(e.g. `Task_el_4`, not the bare schema id `el-4`) -- see the "Node
identity" note below for why. `required_inputs`/`expected_outputs` share
one `AgentIOField` shape (`source_or_destination`, not separate
`source`/`destination` keys) since a field is symmetric -- where it comes
from for an input is the same kind of fact as where it goes for an output:

```json
{
  "node_id": "Task_el_4",
  "verdict": "automatable|partial|not_automatable",
  "step_type": "data_retrieval_transformation",
  "rationale": "string -- must reference this node's actual inputs/outputs/context",
  "agent_spec": {
    "name": "string, human-readable",
    "purpose": "one-sentence description of what the agent does",
    "trigger": "what starts this agent's run (event, schedule, upstream agent handoff)",
    "required_inputs": [
      {"name": "string", "source_or_destination": "which system/prior step/document provides this", "format": "string"}
    ],
    "expected_outputs": [
      {"name": "string", "source_or_destination": "which system/next step consumes this", "format": "string"}
    ],
    "tools_systems_needed": ["string -- specific systems/APIs the agent would need access to"],
    "human_checkpoint": "none|review_before_action|review_after_action|escalation_on_exception",
    "consolidated_from_nodes": ["Task_el_3", "Task_el_4"]
  },
  "not_automatable_reason": null
}
```

`not_automatable_reason` is required (non-null, specific) when `verdict` is
`not_automatable`, and must name which rubric factor drove the decision --
never a generic "requires human involvement" without saying why.

## Grouping/consolidation heuristics (US7.5)

Recommend consolidating sequential nodes into a single agent when:
- They share the same actor/system context and have no human checkpoint
  between them in the original diagram.
- The output of one node is consumed directly as the input of the next with
  no branching decision in between that a human currently makes.

Keep nodes as separate agents when:
- A human currently reviews or approves between them (preserve that
  checkpoint rather than optimizing it away by default).
- They involve fundamentally different tool/system access (e.g. one reads
  from a CRM, the next writes to a finance system) -- separate agents are
  easier to scope, permission, and debug than one broad agent.

## Node identity and source of truth

Evaluate against the **finalized version's BPMN XML** (`app/bpmn/nodes.py`'s
`extract_flow_nodes`), not the live `ProcessSchema` -- a finalized version
is an immutable snapshot (Epic 6) that can already have drifted from the
current schema tables (a manual canvas edit via `PUT /bpmn` never touches
them). Evaluating against the schema instead could silently score nodes
that aren't even in the diagram being finalized, or miss ones that are.
`node_id` in every result is therefore the node's real BPMN element id
from that XML (`Task_el_4`, `Gateway_el_7`, ...) -- this is also what lets
Epic 8's canvas overlay highlight the exact same element bpmn-js already
renders, with no id-translation layer needed (contrast with
`bpmn-chat-ops`'s bare-schema-id-in-the-prompt convention, which exists
for a different reason -- the LLM there is proposing *new* elements that
don't have a BPMN id yet).

## Overlay data format (US7.6)

The blueprint is a separate overlay object keyed by node ID (a list of the
per-node objects above), stored and versioned independently of the BPMN
diagram itself -- never write blueprint data into the BPMN XML directly.
This keeps the finalized as-is diagram (Epic 6) immutable and historically
accurate while the blueprint can be regenerated (US7.7) without touching it.

**Implemented as one row per process** (`BlueprintOverlayModel`,
`app/db/models.py`), the full node list stored as a single JSON blob and
replaced wholesale on regenerate -- no separate version history for the
overlay itself (unlike the diagram's own `VersionModel`), since US7.7 only
asks for "regenerate when the baseline changes," not "compare two past
blueprint runs." `override_blueprint_node` (US7.4's human-override path)
mutates one node's dict within that same JSON blob rather than a separate
table, since the override is just another field on the same per-node
record the LLM produced.
