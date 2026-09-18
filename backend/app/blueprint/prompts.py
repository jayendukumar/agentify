"""Epic 7 / agentic-blueprint-evaluator skill: the system instructions +
JSON response contract for per-node automation feasibility evaluation.
Same prompt-with-explicit-schema-contract pattern as app/chat/prompts.py --
plain ##TOKEN## substitution (not str.format), since the response shape
example below is full of literal JSON braces.
"""

from __future__ import annotations

import json

from app.bpmn.nodes import FlowNodeInfo

_INSTRUCTIONS = """You are evaluating a finalized as-is business process diagram for the Agentic \
Solution Generator project. For every step (node) listed below, decide whether an AI agent could \
perform it, and if so, propose what that agent would need.

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

## Automation decision rubric -- for every node, produce a verdict of "automatable", "partial", \
or "not_automatable", with a rationale grounded in:
1. The step-type classification above (a starting point, not the final word).
2. Whether the inputs the step needs are available as structured data or reliably extractable \
text/documents (information that only exists in someone's head or an unindexed system is \
evidence against "automatable").
3. Whether the step's decision logic can be stated as explicit rules/criteria (favor automation) \
vs. depends on unstated judgment, relationship context, or negotiation (favor "partial" or \
"not_automatable").
4. Whether getting the step wrong has a bounded, correctable cost (favor automation, especially \
with a human-review checkpoint) vs. an authority/liability cost only a specific accountable \
person can bear (favor "not_automatable" regardless of how mechanical the step looks).

Never default to "automatable" purely because the step-type table leans that way -- ground the \
rationale in this specific node's actual label, predecessors/successors, and lane/actor context \
below, not just its type label.

## Agent spec -- every "automatable" or "partial" node needs a proposed agent_spec (name, \
purpose, trigger, required_inputs, expected_outputs, tools_systems_needed, human_checkpoint, \
consolidated_from_nodes). A "not_automatable" node gets agent_spec: null and a required, specific \
not_automatable_reason naming which rubric factor drove the decision -- never a generic "requires \
human involvement" with no reason stated.

## Grouping/consolidation -- recommend consolidating sequential nodes into one agent (same \
agent_spec, with consolidated_from_nodes listing every node id it covers) when they share the \
same actor/lane and have no human checkpoint between them in the diagram, and one node's output \
is consumed directly by the next with no branching decision a human currently makes in between. \
Keep nodes as separate agents when a human currently reviews/approves between them (preserve that \
checkpoint, don't optimize it away by default), or when they need fundamentally different tool/ \
system access. When you consolidate, still return ONE result entry per node_id (every node in the \
diagram must appear exactly once in your response) -- give each consolidated node the SAME \
agent_spec object, including the same consolidated_from_nodes list, rather than omitting any of \
the nodes it covers.

## Process nodes (JSON) -- you must return exactly one result for each node id below, no more, no \
fewer. Each node's predecessors/successors are given by their own label plus any flow condition, \
for context on what feeds into and out of this step:
##NODES_JSON##

Respond with a single JSON object matching exactly this shape (no prose, no markdown code fences, \
just the JSON object):
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
      "not_automatable_reason": null | "string -- required and specific when verdict is not_automatable"
    }
  ]
}
"""


def _node_context(node: FlowNodeInfo) -> dict:
    return {
        "id": node.id,
        "label": node.label,
        "bpmn_type": node.bpmn_type,
        "actor_or_lane": node.lane_name,
        "predecessors": [{"label": p.label, "condition": p.condition} for p in node.predecessors],
        "successors": [{"label": s.label, "condition": s.condition} for s in node.successors],
    }


def build_system_prompt(nodes: list[FlowNodeInfo]) -> str:
    nodes_json = json.dumps([_node_context(n) for n in nodes], indent=2)
    return _INSTRUCTIONS.replace("##NODES_JSON##", nodes_json)
