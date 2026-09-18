import json

import pytest

from app.blueprint.service import BlueprintServiceError, evaluate_blueprint
from app.bpmn.nodes import FlowNodeInfo
from app.llm.types import ChatCompletionResult, Usage


def _nodes() -> list[FlowNodeInfo]:
    return [
        FlowNodeInfo(id="Task_a", label="Fetch invoice", bpmn_type="serviceTask", lane_name="Finance"),
        FlowNodeInfo(id="Task_b", label="Approve payment", bpmn_type="userTask", lane_name="Finance"),
    ]


def _result(node_id: str, verdict: str = "automatable") -> dict:
    return {
        "node_id": node_id,
        "verdict": verdict,
        "step_type": "data_retrieval_transformation",
        "rationale": f"{node_id} rationale",
        "agent_spec": None,
        "not_automatable_reason": "needs a human" if verdict == "not_automatable" else None,
    }


def _completion(text: str | None, finish_reason: str = "stop") -> ChatCompletionResult:
    return ChatCompletionResult(
        text=text, finish_reason=finish_reason, usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="m"
    )


class _FakeLLM:
    def __init__(self, result: ChatCompletionResult) -> None:
        self._result = result
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self._result


@pytest.mark.asyncio
async def test_evaluate_blueprint_returns_one_result_per_node_in_input_order():
    payload = {"nodes": [_result("Task_b"), _result("Task_a", verdict="not_automatable")]}
    llm = _FakeLLM(_completion(json.dumps(payload)))

    results = await evaluate_blueprint(llm, flow_nodes=_nodes())

    assert [r.node_id for r in results] == ["Task_a", "Task_b"]  # order of flow_nodes, not LLM response
    assert results[0].verdict == "not_automatable"
    assert results[0].not_automatable_reason == "needs a human"


@pytest.mark.asyncio
async def test_evaluate_blueprint_drops_unknown_node_ids_from_response():
    payload = {"nodes": [_result("Task_a"), _result("Task_b"), _result("Task_ghost")]}
    llm = _FakeLLM(_completion(json.dumps(payload)))

    results = await evaluate_blueprint(llm, flow_nodes=_nodes())

    assert {r.node_id for r in results} == {"Task_a", "Task_b"}


@pytest.mark.asyncio
async def test_evaluate_blueprint_raises_when_a_real_node_is_missing():
    payload = {"nodes": [_result("Task_a")]}  # Task_b never evaluated
    llm = _FakeLLM(_completion(json.dumps(payload)))

    with pytest.raises(BlueprintServiceError, match="Task_b"):
        await evaluate_blueprint(llm, flow_nodes=_nodes())


@pytest.mark.asyncio
async def test_evaluate_blueprint_raises_on_empty_response_text():
    llm = _FakeLLM(_completion(None, finish_reason="length"))

    with pytest.raises(BlueprintServiceError):
        await evaluate_blueprint(llm, flow_nodes=_nodes())


@pytest.mark.asyncio
async def test_evaluate_blueprint_raises_on_invalid_json():
    llm = _FakeLLM(_completion("not json"))

    with pytest.raises(BlueprintServiceError):
        await evaluate_blueprint(llm, flow_nodes=_nodes())
