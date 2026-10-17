"""Epic 39, US39.1: the executor extraction changed nothing.

Each case below runs a scripted model through `run_scenario` and records
everything observable: the steps, the outcome, the resume state, and every
request sent to the model. `fixtures/executor_replay.json` was recorded
from the twin engine *before* the loop moved to app/runtime/executor.py,
so any difference here is a behaviour change, not a refactor.

Re-record (only for a deliberate behaviour change) with
`RECORD_EXECUTOR_REPLAY=1 python -m pytest tests/test_runtime_replay.py`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.llm.types import ChatCompletionResult, ToolCall, Usage
from app.schemas.agents import AgentDefinition, EscalationPolicy, ResourcePermission, RuntimeGuardrails, ToolContract
from app.schemas.environment import FaultConfig, SimulatedSystemData, TwinEnvironment
from app.schemas.twin import (
    InferredToolSchema,
    StaticResponseRule,
    TwinExpectedStep,
    TwinHumanCheckpointConfig,
    TwinScenario,
    TwinSystemStub,
)
from app.twin.engine import RunCancelled, run_scenario

FIXTURE = Path(__file__).parent / "fixtures" / "executor_replay.json"
_NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


class ScriptedLLM:
    """Returns the scripted results in order and records each request."""

    def __init__(self, results: list[ChatCompletionResult]):
        self._results = list(results)
        self.requests: list[dict[str, Any]] = []

    async def complete(self, messages, **kwargs):
        self.requests.append({
            "messages": [m.model_dump(mode="json") for m in messages],
            "kwargs": {k: (v.model_dump(mode="json") if hasattr(v, "model_dump") else
                           [t.model_dump(mode="json") for t in v] if isinstance(v, list) else v)
                       for k, v in kwargs.items()},
        })
        if not self._results:
            raise AssertionError("the scripted model ran out of responses")
        return self._results.pop(0)


def _result(*, calls: list[tuple[str, dict]] | None = None, text: str | None = None, tokens: int = 10) -> ChatCompletionResult:
    tool_calls = [ToolCall(id=f"call_{i}", name=name, arguments=args) for i, (name, args) in enumerate(calls or [])]
    return ChatCompletionResult(
        text=text, tool_calls=tool_calls, finish_reason="tool_calls" if tool_calls else "stop",
        usage=Usage(input_tokens=tokens - 2, output_tokens=2, total_tokens=tokens), model="test-model",
    )


def _crm() -> InferredToolSchema:
    return InferredToolSchema(
        system_name="CRM", tool_name="lookup_customer", description="Look up a customer.",
        parameters={"type": "object", "properties": {"id": {"type": "string"}}},
        response_shape_description="An object with a tier.",
    )


def _erp() -> InferredToolSchema:
    return InferredToolSchema(
        system_name="ERP", tool_name="post_invoice", description="Post an invoice.",
        parameters={"type": "object", "properties": {"amount": {"type": "number"}}},
        response_shape_description="An object with a document number.",
    )


def _artifact(**overrides) -> AgentDefinition:
    defaults = dict(
        name="Invoice agent", purpose="Check invoices", trigger="Invoice received",
        system_prompt="You check invoices.", tools_systems_needed=["CRM", "ERP"], model="test-model",
        output_schema=[{"name": "decision", "source_or_destination": "AP", "format": "string"}],
    )
    defaults.update(overrides)
    return AgentDefinition(**defaults)


def _scenario(**overrides) -> TwinScenario:
    defaults = dict(
        id="sc", agent_artifact_id="a", name="case", inputs={"invoice_id": "INV-1"},
        system_stubs={"CRM": TwinSystemStub(mode="static", static_responses=[
            StaticResponseRule(match={}, response={"tier": "gold"})])},
        human_checkpoint_config=TwinHumanCheckpointConfig(), expected_steps=[], expected_outputs={},
        created_at=_NOW,
    )
    defaults.update(overrides)
    return TwinScenario(**defaults)


def _environment(**overrides) -> TwinEnvironment:
    defaults = dict(
        id="env", process_id="p", name="env", version=1, systems=[], datasets={}, personas={},
        events=[], faults={}, created_at=_NOW, updated_at=_NOW,
    )
    defaults.update(overrides)
    return TwinEnvironment(**defaults)


_LOOKUP = ("lookup_customer", {"id": "C-1"})
_POST = ("post_invoice", {"amount": 120})
_FINAL = '{"decision": "approve"}'


def _cases() -> dict[str, dict[str, Any]]:
    contracts = [
        ToolContract(system_name="CRM", resource="customer", action="read"),
        ToolContract(system_name="ERP", resource="invoice", action="update", side_effect="mutating"),
    ]
    return {
        "happy_path_graded": dict(
            results=[_result(calls=[_LOOKUP]), _result(text=_FINAL)],
            scenario=_scenario(
                expected_steps=[TwinExpectedStep(kind="tool_call", target="CRM")],
                expected_outputs={"decision": "approve"},
            ),
        ),
        "policy_denied": dict(
            artifact=_artifact(tool_contracts=contracts, permissions=[
                ResourcePermission(resource="customer", actions={"read": True})]),
            results=[_result(calls=[_LOOKUP, _POST]), _result(text=_FINAL)],
            scenario=_scenario(system_stubs={
                "CRM": TwinSystemStub(mode="static", static_responses=[StaticResponseRule(match={}, response={"tier": "gold"})]),
                "ERP": TwinSystemStub(mode="static", static_responses=[StaticResponseRule(match={}, response={"doc": "1"})]),
            }),
        ),
        "environment_and_scenario_faults": dict(
            results=[_result(calls=[_LOOKUP]), _result(calls=[_POST]), _result(text=_FINAL)],
            scenario=_scenario(faults={"CRM": [FaultConfig(type="http_500")]}),
            environment=_environment(faults={"ERP": [FaultConfig(type="timeout", probability=1.0)]}),
        ),
        "proxy_tool_and_usage": dict(
            results=[_result(calls=[_POST]), _result(text='{"doc": "5100001"}', tokens=7), _result(text=_FINAL)],
            scenario=_scenario(),
        ),
        "human_checkpoint_seeded": dict(
            artifact=_artifact(human_checkpoint="review_before_action"),
            results=[
                _result(calls=[("request_human_decision", {"summary": "pay", "proposed_action": {"amount": 120}})]),
                _result(text=_FINAL),
            ],
            scenario=_scenario(human_checkpoint_config=TwinHumanCheckpointConfig(
                mode="probability", approve_probability=0.5, seed=7)),
        ),
        "manual_checkpoint_suspends": dict(
            artifact=_artifact(human_checkpoint="review_before_action"),
            results=[_result(calls=[_LOOKUP, ("request_human_decision", {"summary": "pay", "proposed_action": {}}), _POST])],
            scenario=_scenario(human_checkpoint_config=TwinHumanCheckpointConfig(mode="manual")),
            resume=dict(decision="reject", results=[_result(text='{"doc": "1"}', tokens=5), _result(text='{"decision": "reject"}')]),
        ),
        "repair_turn": dict(
            results=[_result(text="Here you go: decision approve"), _result(text=_FINAL)],
            scenario=_scenario(),
        ),
        "fenced_json_final": dict(
            results=[_result(text='```json\n{"decision": "approve"}\n```')],
            scenario=_scenario(),
        ),
        "max_steps_escalates": dict(
            artifact=_artifact(
                guardrails=RuntimeGuardrails(max_steps=2),
                escalation_policy=EscalationPolicy(escalate_when=["stuck"]),
            ),
            results=[_result(calls=[_LOOKUP]), _result(calls=[("lookup_customer", {"id": "C-2"})])],
            scenario=_scenario(),
        ),
        "tool_call_limit_fails": dict(
            artifact=_artifact(guardrails=RuntimeGuardrails(max_tool_calls=1)),
            results=[_result(calls=[_LOOKUP, ("lookup_customer", {"id": "C-2"})])],
            scenario=_scenario(),
        ),
        "loop_detection": dict(
            artifact=_artifact(guardrails=RuntimeGuardrails(retry_max_attempts=0)),
            results=[_result(calls=[_LOOKUP]), _result(calls=[_LOOKUP])],
            scenario=_scenario(),
        ),
        "unknown_tool_fails": dict(
            results=[_result(calls=[("delete_everything", {})])],
            scenario=_scenario(),
        ),
        "no_json_after_repair_fails": dict(
            results=[_result(text="not json"), _result(text="still not json")],
            scenario=_scenario(),
        ),
        "dataset_decision_and_routing": dict(
            results=[_result(text='{"decision": "reject", "route": "Yes"}')],
            scenario=_scenario(expected_decision="approve", decision_field="decision"),
            routing=("Is it valid?", ["Yes", "No"]),
            node_id="Task_1",
        ),
        "sample_data_records": dict(
            results=[_result(calls=[("crm_records", {"action": "lookup", "id": "C-1"})]), _result(text=_FINAL)],
            scenario=_scenario(system_stubs={}),
            environment=_environment(system_data={"CRM": SimulatedSystemData(
                records=[{"id": "C-1", "tier": "gold"}], lookup_fields=["id"], operations=["lookup"])}),
        ),
        "cancelled_mid_run": dict(
            results=[_result(calls=[_LOOKUP]), _result(text=_FINAL)],
            scenario=_scenario(),
            cancel_at=3,
        ),
        "stub_latency": dict(
            results=[_result(calls=[_LOOKUP]), _result(text=_FINAL)],
            scenario=_scenario(stub_latency_ms=1),
        ),
    }


def _normalize(value: Any) -> Any:
    """Drops wall-clock values, which differ between any two runs."""
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items() if k not in ("occurred_at", "elapsed_seconds")}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _outcome(outcome) -> dict[str, Any]:
    return _normalize({
        "status": outcome.status,
        "steps": [s.model_dump(mode="json") for s in outcome.steps],
        "final_output": outcome.final_output,
        "deviations": [d.model_dump(mode="json") for d in outcome.deviations],
        "graded_passed": outcome.graded_passed,
        "total_cost_usd": outcome.total_cost_usd,
        "total_tokens": outcome.total_tokens,
        "turns_used": outcome.turns_used,
        "resume_state": outcome.resume_state,
    })


async def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    tool_schemas = {"CRM": _crm(), "ERP": _erp()}
    llm = ScriptedLLM(case["results"])
    phases: list[str] = []

    async def on_phase(status):
        phases.append(status)

    async def on_step(step):
        if step.seq == case.get("cancel_at"):
            raise RunCancelled()

    common = dict(
        artifact=case.get("artifact") or _artifact(), tool_schemas=tool_schemas, scenario=case["scenario"],
        max_turns=6, environment=case.get("environment"), node_id=case.get("node_id"),
        routing=case.get("routing"), on_phase=on_phase, on_step=on_step,
    )
    first = await run_scenario(llm, **common)
    record = {"first": _outcome(first), "phases": phases, "requests": _normalize(llm.requests)}
    if "resume" in case:
        llm2 = ScriptedLLM(case["resume"]["results"])
        second = await run_scenario(
            llm2, **common, resume_state=first.resume_state, human_decision=case["resume"]["decision"],
        )
        record["resumed"] = _outcome(second)
        record["resumed_requests"] = _normalize(llm2.requests)
    return record


@pytest.mark.asyncio
async def test_executor_replay_matches_recording():
    observed = {name: await _run_case(case) for name, case in _cases().items()}
    if os.environ.get("RECORD_EXECUTOR_REPLAY") == "1":
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_text(json.dumps(observed, indent=1, sort_keys=True), encoding="utf-8")
        pytest.skip("recorded the executor replay fixture")
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert sorted(observed) == sorted(recorded)
    for name in recorded:
        assert json.loads(json.dumps(observed[name], sort_keys=True)) == recorded[name], name
