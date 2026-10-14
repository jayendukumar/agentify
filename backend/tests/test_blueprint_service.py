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


class _ChunkAwareLLM:
    """Answers each call for exactly the node ids its prompt asks about,
    with an agent spec that tries to consolidate across the whole diagram."""

    def __init__(self, all_ids: list[str]) -> None:
        self.all_ids = all_ids
        self.prompts: list[str] = []

    async def complete(self, messages, **kwargs):
        prompt = messages[0].content
        self.prompts.append(prompt)
        evaluated = prompt.split("context only")[0]
        ids = [i for i in self.all_ids if f'"id": "{i}"' in evaluated]
        spec = {"name": "A", "purpose": "p", "trigger": "t", "required_inputs": [], "expected_outputs": [],
                "tools_systems_needed": [], "human_checkpoint": "none", "consolidated_from_nodes": self.all_ids[:12]}
        nodes = [{**_result(i), "agent_spec": spec} for i in ids]
        return _completion(json.dumps({"nodes": nodes}))


@pytest.mark.asyncio
async def test_large_diagrams_are_evaluated_in_chunks_with_the_whole_diagram_as_context():
    """Epic 36: a 20-node diagram in one call overran the output cap live."""
    nodes = [FlowNodeInfo(id=f"Task_{i:02d}", label=f"Step {i}", bpmn_type="task", lane_name="AP") for i in range(20)]
    llm = _ChunkAwareLLM([n.id for n in nodes])

    results = await evaluate_blueprint(llm, flow_nodes=nodes)

    assert len(llm.prompts) == 3  # 8 + 8 + 4
    assert [r.node_id for r in results] == [n.id for n in nodes]
    assert "Step 19" in llm.prompts[0]  # every chunk sees the rest of the diagram
    # Consolidation is trimmed to the chunk the model evaluated.
    assert results[0].agent_spec.consolidated_from_nodes == [f"Task_{i:02d}" for i in range(8)]
    assert results[9].agent_spec.consolidated_from_nodes == [f"Task_{i:02d}" for i in range(8, 12)]


@pytest.mark.asyncio
async def test_a_truncated_reply_says_so():
    llm = _FakeLLM(_completion('{"nodes": [{"node_id": "Task_a", "rationale": "cut', finish_reason="length"))
    with pytest.raises(BlueprintServiceError, match="cut off at the output limit"):
        await evaluate_blueprint(llm, flow_nodes=_nodes())


@pytest.mark.asyncio
async def test_one_malformed_reply_is_retried():
    """Seen live: one chunk's reply missing a comma failed the whole blueprint."""
    good = _completion(json.dumps({"nodes": [_result("Task_a"), _result("Task_b")]}))
    replies = iter([_completion('{"nodes": [ {"node_id": "Task_a" "verdict": 1}]}'), good])

    class _Flaky(_FakeLLM):
        async def complete(self, messages, **kwargs):
            self.calls.append(kwargs)
            return next(replies)

    llm = _Flaky(good)
    results = await evaluate_blueprint(llm, flow_nodes=_nodes())
    assert [r.node_id for r in results] == ["Task_a", "Task_b"] and len(llm.calls) == 2
