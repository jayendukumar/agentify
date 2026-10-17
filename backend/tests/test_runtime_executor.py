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
