"""Epic 12, US12.1/US12.2: deterministic mapping from a blueprint node's
AgentSpec (Epic 7) to a portable AgentDefinition. No LLM call here -- the
blueprint evaluation (app/blueprint/service.py) already did the judgment
work of deciding what the agent should do; this module only renders that
decision into a runnable system prompt and I/O schema, the same way
app/bpmn/builder.py deterministically renders a ProcessSchema into BPMN
XML rather than asking an LLM to write XML. Being deterministic also makes
this trivially unit-testable without mocking an LLM call.
"""

from __future__ import annotations

from app.config import get_settings
from app.schemas.agents import AgentDefinition
from app.schemas.blueprint import BlueprintNodeResult

_HUMAN_CHECKPOINT_INSTRUCTIONS = {
    "none": "Complete the task end-to-end without requiring human review.",
    "review_before_action": (
        "Prepare the result and stop for human review and approval before taking any action "
        "that has an effect outside this agent."
    ),
    "review_after_action": (
        "Complete the task, then flag the result for human review after acting -- "
        "do not wait for approval before acting."
    ),
    "escalation_on_exception": (
        "Complete the task normally; if you encounter a case outside your expected inputs "
        "or rules, stop and escalate to a human rather than guessing."
    ),
}


class AgentGenerationError(Exception):
    pass


def resolve_group(overlay_nodes: list[BlueprintNodeResult], node_id: str) -> list[BlueprintNodeResult]:
    """US12.5: the group a "Generate agent" click resolves to. Grouping
    membership already lives on the blueprint's own per-node agent_spec
    (`consolidated_from_nodes`, US7.5) -- this reads that rather than
    asking the user to redeclare it, so clicking "Generate" from any node
    already inside a consolidated group resolves to the same group and
    (via group_key_for) the same artifact, regardless of which member node
    was actually clicked.
    """
    primary = next((n for n in overlay_nodes if n.node_id == node_id), None)
    if primary is None:
        raise AgentGenerationError(f"Node '{node_id}' not found in the current blueprint")
    if primary.agent_spec is None:
        raise AgentGenerationError(
            f"Node '{node_id}' has no agent spec -- only automatable or partial nodes can generate an agent"
        )

    group_ids = set(primary.agent_spec.consolidated_from_nodes or [node_id])
    group_ids.add(node_id)
    group = [n for n in overlay_nodes if n.node_id in group_ids]
    return group or [primary]


def group_key_for(node_ids: list[str]) -> str:
    return "|".join(sorted(node_ids))


def build_agent_definition(nodes: list[BlueprintNodeResult], *, primary_node_id: str) -> AgentDefinition:
    """`nodes` is the full consolidated group (one or more blueprint node
    results, from resolve_group); `primary_node_id` is the node the user
    actually triggered generation from -- its own agent_spec is
    authoritative for the definition's content, since it's guaranteed to
    exist (only automatable/partial nodes expose the "Generate agent"
    action), even if the LLM only fully populated one node's spec in an
    otherwise-consolidated group.
    """
    primary = next((n for n in nodes if n.node_id == primary_node_id), None)
    if primary is None or primary.agent_spec is None:
        raise AgentGenerationError(f"Node '{primary_node_id}' has no agent spec to generate from")

    spec = primary.agent_spec
    return AgentDefinition(
        name=spec.name,
        purpose=spec.purpose,
        trigger=spec.trigger,
        system_prompt=_build_system_prompt(nodes, primary),
        input_schema=spec.required_inputs,
        output_schema=spec.expected_outputs,
        tools_systems_needed=spec.tools_systems_needed,
        human_checkpoint=spec.human_checkpoint,
        # US12's own scope note: default to the product's current LLM call
        # model rather than building a per-task model-selection engine,
        # which is speculative scope beyond what Epic 12 asks for.
        model=get_settings().llm_model,
    )


def _build_system_prompt(nodes: list[BlueprintNodeResult], primary: BlueprintNodeResult) -> str:
    spec = primary.agent_spec
    assert spec is not None

    lines = [
        f"You are {spec.name}, an automation agent for one step of a business process.",
        "",
        f"Purpose: {spec.purpose}",
        f"Trigger: {spec.trigger}",
    ]

    if len(nodes) > 1:
        covered = ", ".join(sorted(n.node_id for n in nodes))
        lines.append(
            f"This agent consolidates {len(nodes)} consecutive process steps into one run ({covered}) "
            "-- perform them in order as a single unit of work, not as separate handoffs."
        )

    lines.append("")
    lines.append(f"Context from the process evaluation: {primary.rationale}")

    if spec.required_inputs:
        lines.append("")
        lines.append("Required inputs:")
        for field in spec.required_inputs:
            lines.append(f"- {field.name} (from {field.source_or_destination}, format: {field.format})")

    if spec.expected_outputs:
        lines.append("")
        lines.append("Expected outputs:")
        for field in spec.expected_outputs:
            lines.append(f"- {field.name} (to {field.source_or_destination}, format: {field.format})")

    if spec.tools_systems_needed:
        lines.append("")
        lines.append("You have access to: " + ", ".join(spec.tools_systems_needed) + ".")
        lines.append("Do not act outside these systems; escalate if the task requires something else.")

    lines.append("")
    lines.append(
        _HUMAN_CHECKPOINT_INSTRUCTIONS.get(
            spec.human_checkpoint, "Escalate to a human if you are ever uncertain how to proceed."
        )
    )

    return "\n".join(lines)
