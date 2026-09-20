import pytest

from app.agents.generation import (
    AgentGenerationError,
    build_agent_definition,
    group_key_for,
    resolve_group,
)
from app.schemas.blueprint import AgentIOField, AgentSpec, BlueprintNodeResult


def _node(node_id: str, *, consolidated: list[str] | None = None, human_checkpoint: str = "none") -> BlueprintNodeResult:
    return BlueprintNodeResult(
        node_id=node_id,
        verdict="automatable",
        step_type="data_retrieval_transformation",
        rationale=f"{node_id} is mechanical.",
        agent_spec=AgentSpec(
            name=f"{node_id} Agent",
            purpose="Do the thing",
            trigger="Upstream step completes",
            required_inputs=[AgentIOField(name="record_id", source_or_destination="CRM", format="string")],
            expected_outputs=[AgentIOField(name="summary", source_or_destination="ticketing", format="text")],
            tools_systems_needed=["CRM API"],
            human_checkpoint=human_checkpoint,
            consolidated_from_nodes=consolidated or [],
        ),
    )


def _not_automatable(node_id: str) -> BlueprintNodeResult:
    return BlueprintNodeResult(
        node_id=node_id,
        verdict="not_automatable",
        step_type="approval_compliance_signoff",
        rationale="Needs sign-off.",
        not_automatable_reason="Requires an accountable human.",
    )


def test_resolve_group_single_node_has_no_consolidation():
    nodes = [_node("a"), _not_automatable("b")]
    group = resolve_group(nodes, "a")
    assert [n.node_id for n in group] == ["a"]


def test_resolve_group_consolidated_group():
    nodes = [_node("a", consolidated=["a", "b"]), _node("b", consolidated=["a", "b"])]
    group = resolve_group(nodes, "a")
    assert {n.node_id for n in group} == {"a", "b"}


def test_resolve_group_unknown_node_raises():
    with pytest.raises(AgentGenerationError):
        resolve_group([_node("a")], "missing")


def test_resolve_group_not_automatable_node_raises():
    with pytest.raises(AgentGenerationError):
        resolve_group([_not_automatable("b")], "b")


def test_group_key_is_order_independent():
    assert group_key_for(["b", "a"]) == group_key_for(["a", "b"]) == "a|b"


def test_build_agent_definition_maps_spec_fields():
    node = _node("a")
    definition = build_agent_definition([node], primary_node_id="a")

    assert definition.name == "a Agent"
    assert definition.purpose == "Do the thing"
    assert definition.input_schema[0].name == "record_id"
    assert definition.output_schema[0].name == "summary"
    assert definition.tools_systems_needed == ["CRM API"]
    assert "a Agent" in definition.system_prompt
    assert "CRM API" in definition.system_prompt
    assert definition.model


def test_build_agent_definition_notes_consolidation_in_prompt():
    nodes = [_node("a", consolidated=["a", "b"]), _node("b", consolidated=["a", "b"])]
    definition = build_agent_definition(nodes, primary_node_id="a")
    assert "consolidates 2 consecutive process steps" in definition.system_prompt
    assert "a, b" in definition.system_prompt


def test_build_agent_definition_reflects_human_checkpoint():
    escalate = build_agent_definition([_node("a", human_checkpoint="escalation_on_exception")], primary_node_id="a")
    assert "escalate to a human" in escalate.system_prompt.lower()

    review = build_agent_definition([_node("a", human_checkpoint="review_before_action")], primary_node_id="a")
    assert "before taking any action" in review.system_prompt.lower()


def test_build_agent_definition_missing_spec_raises():
    with pytest.raises(AgentGenerationError):
        build_agent_definition([_not_automatable("b")], primary_node_id="b")
