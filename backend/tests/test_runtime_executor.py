"""Epic 39, US39.1: the shared executor, driven by ports that aren't the
twin's -- the way production will drive it. tests/test_runtime_replay.py
proves the twin's behaviour didn't change when the loop moved here."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.llm.types import ChatCompletionResult, ToolCall, ToolDefinition, Usage
from app.runtime.executor import ExecutionError, ToolResult, execute
from app.schemas.agents import AgentDefinition, ResourcePermission, ToolContract

RUNTIME = Path(__file__).resolve().parents[1] / "app" / "runtime"


class ScriptedLLM:
    def __init__(self, *results: ChatCompletionResult):
        self.results = list(results)
        self.operations: list[str] = []

    async def complete(self, messages, **kwargs):
        self.operations.append(kwargs["operation"])
        return self.results.pop(0)


def _result(*, calls=None, text=None) -> ChatCompletionResult:
    tool_calls = [ToolCall(id=f"c{i}", name=n, arguments=a) for i, (n, a) in enumerate(calls or [])]
    return ChatCompletionResult(
        text=text, tool_calls=tool_calls, finish_reason="tool_calls" if tool_calls else "stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="test-model",
    )


class RecordingTools:
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def call(self, system_name, call):
        self.calls.append((system_name, call.arguments))
        if self.fail:
            raise ExecutionError(f"{system_name} is unreachable")
        return ToolResult(response={"ok": True})


class Inbox:
    """Like production's review inbox: never decides on the spot."""

    def __init__(self):
        self.asked: list[dict] = []

    async def decide(self, proposed_action):
        self.asked.append(proposed_action)
        return None


def _artifact(**overrides) -> AgentDefinition:
    defaults = dict(
        name="a", purpose="p", trigger="t", system_prompt="s", model="test-model",
        tools_systems_needed=["CRM", "ERP"],
        tool_contracts=[
            ToolContract(system_name="CRM", resource="customer", action="read"),
            ToolContract(system_name="ERP", resource="invoice", action="update", side_effect="mutating"),
        ],
        permissions=[ResourcePermission(resource="customer", actions={"read": True})],
    )
    defaults.update(overrides)
    return AgentDefinition(**defaults)


_TOOLS = {
    "CRM": ToolDefinition(name="crm", description="", parameters={"type": "object"}),
    "ERP": ToolDefinition(name="erp", description="", parameters={"type": "object"}),
}


def _run(llm, tools, human, artifact=None, **kwargs):
    return execute(
        llm, artifact=artifact or _artifact(), tools=_TOOLS, tool_port=tools, human_port=human,
        inputs={"id": "1"}, instructions="Production run.", output_fields=["decision"], max_turns=5,
        operation="runtime_execution", **kwargs,
    )


def test_runtime_imports_nothing_from_the_twin_but_the_gateway():
    for path in RUNTIME.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            for module in modules:
                if module.startswith("app.twin"):
                    assert module == "app.twin.gateway", f"{path.name} imports {module}"


@pytest.mark.asyncio
async def test_a_denied_call_never_reaches_the_tool_port():
    llm = ScriptedLLM(_result(calls=[("crm", {"id": "1"}), ("erp", {"amount": 5})]), _result(text='{"decision": "x"}'))
    tools = RecordingTools()
    outcome = await _run(llm, tools, Inbox())
    assert tools.calls == [("CRM", {"id": "1"})]
    assert outcome.status == "COMPLETED"
    assert [d.reason for d in outcome.policy_deviations] == [
        "Tool call to 'ERP' denied by policy: agent lacks 'update' permission on resource 'invoice' (required by tool 'ERP')"
    ]
    assert llm.operations == ["runtime_execution", "runtime_execution"]


@pytest.mark.asyncio
async def test_a_port_failure_ends_the_run_failed():
    llm = ScriptedLLM(_result(calls=[("crm", {"id": "1"})]))
    outcome = await _run(llm, RecordingTools(fail=True), Inbox())
    assert outcome.status == "FAILED"
    assert outcome.terminal_deviation.reason == "CRM is unreachable"
    failed = [s for s in outcome.steps if s.type == "TOOL_FAILED"]
    assert failed[0].target == "CRM" and failed[0].reason == "CRM is unreachable"


@pytest.mark.asyncio
async def test_a_checkpoint_waits_for_a_person_and_resumes():
    artifact = _artifact(human_checkpoint="review_before_action")
    ask = ("request_human_decision", {"summary": "pay", "proposed_action": {"amount": 5}})
    inbox = Inbox()
    first = await _run(ScriptedLLM(_result(calls=[ask])), RecordingTools(), inbox, artifact=artifact)
    assert first.status == "WAITING_FOR_HUMAN"
    assert inbox.asked == [{"amount": 5}]

    second = await _run(
        ScriptedLLM(_result(text='{"decision": "approve"}')), RecordingTools(), inbox, artifact=artifact,
        resume_state=first.resume_state, human_decision="approve",
    )
    assert second.status == "COMPLETED"
    assert second.final_output == {"decision": "approve"}
    assert [s.type for s in second.steps if s.type.startswith("HUMAN")] == ["HUMAN_APPROVAL_REQUESTED", "HUMAN_APPROVED"]
    assert inbox.asked == [{"amount": 5}]  # the resumed call wasn't asked again


@pytest.mark.asyncio
async def test_twin_and_production_ports_decide_alike():
    """US39.8: the same model replies through the twin's ports and through
    production-style ports give the same policy decisions and outcome, and
    the same instructions apart from their header."""
    from datetime import datetime, timezone

    from app.runtime.instructions import run_instructions
    from app.schemas.twin import InferredToolSchema, StaticResponseRule, TwinScenario, TwinSystemStub
    from app.twin.engine import run_scenario

    artifact = _artifact(output_schema=[{"name": "decision", "source_or_destination": "AP", "format": "string"}])
    replies = lambda: [  # noqa: E731
        _result(calls=[("crm", {"id": "1"}), ("erp", {"amount": 5})]),
        _result(calls=[("crm", {"id": "1"})]),
        _result(text='{"decision": "approve"}'),
    ]
    schemas = {name: InferredToolSchema(system_name=name, tool_name=tool.name, description="", parameters=tool.parameters,
                                        response_shape_description="") for name, tool in _TOOLS.items()}
    stub = TwinSystemStub(mode="static", static_responses=[StaticResponseRule(match={}, response={"ok": True})])
    scenario = TwinScenario(id="s", agent_artifact_id="a", name="n", inputs={"id": "1"},
                            system_stubs={"CRM": stub, "ERP": stub}, created_at=datetime.now(timezone.utc))
    twin_llm = ScriptedLLM(*replies())
    captured = []

    async def capture(messages, **kwargs):
        captured.append(messages[1].content)
        return await twin_llm_complete(messages, **kwargs)

    twin_llm_complete = twin_llm.complete
    twin_llm.complete = capture
    twin = await run_scenario(twin_llm, artifact=artifact, tool_schemas=schemas, scenario=scenario, max_turns=5)
    live = await execute(
        ScriptedLLM(*replies()), artifact=artifact, tools=_TOOLS, tool_port=RecordingTools(), human_port=Inbox(),
        inputs={"id": "1"}, output_fields=["decision"], max_turns=5, operation="runtime_execution",
        instructions=run_instructions(has_checkpoint=False, output_fields=["decision"], test_run=False),
    )

    def decisions(steps):
        return [(s.target, s.denied) for s in steps if s.type == "POLICY_CHECK"]

    assert decisions(twin.steps) == decisions(live.steps) == [("CRM", False), ("ERP", True), ("CRM", False)]
    assert twin.status == live.status == "COMPLETED"
    live_text = run_instructions(has_checkpoint=False, output_fields=["decision"], test_run=False)
    assert captured[0].splitlines()[1:] == live_text.splitlines()[1:]
    assert captured[0].splitlines()[0] != live_text.splitlines()[0]


@pytest.mark.asyncio
async def test_review_before_action_is_enforced_not_just_asked_for():
    """Found live: told to stop for review, the model wrote to production
    without asking. The gateway now denies a data-changing call until a
    person approved in this run; lookups stay allowed."""
    artifact = _artifact(human_checkpoint="review_before_action",
                         permissions=[ResourcePermission(resource="customer", actions={"read": True}),
                                      ResourcePermission(resource="invoice", actions={"update": True})])
    ask = ("request_human_decision", {"summary": "post it", "proposed_action": {"amount": 5}})
    tools = RecordingTools()
    first = await _run(
        ScriptedLLM(_result(calls=[("crm", {"id": "1"}), ("erp", {"amount": 5})]), _result(calls=[ask])),
        tools, Inbox(), artifact=artifact,
    )
    denied = [s for s in first.steps if s.type == "POLICY_CHECK" and s.denied]
    assert [s.target for s in denied] == ["ERP"] and "approval first" in denied[0].reason
    assert tools.calls == [("CRM", {"id": "1"})] and first.status == "WAITING_FOR_HUMAN"

    second = await _run(ScriptedLLM(_result(calls=[("erp", {"amount": 5})]), _result(text='{"decision": "x"}')),
                        tools, Inbox(), artifact=artifact, resume_state=first.resume_state, human_decision="approve")
    assert tools.calls[-1] == ("ERP", {"amount": 5}) and second.status == "COMPLETED"
