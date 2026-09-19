import json

import pytest

from app.db import repository
from app.db.session import get_session_factory
from app.gap_analysis.service import GapAnalysisServiceError, analyze_gaps, run_gap_analysis
from app.llm.types import ChatCompletionResult, Usage
from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _schema(document_id: str = "d") -> ProcessSchema:
    ref = SourceRef(document_id=document_id, location="p1", excerpt="x")
    return ProcessSchema(
        process_name="P",
        elements=[
            ProcessElement(id="a", type="task", label="Classify requirements", source_refs=[ref], confidence="high"),
            ProcessElement(id="b", type="task", label="Issue joining pack", source_refs=[ref], confidence="high"),
        ],
        flows=[ProcessFlow(id="f1", **{"from": "a"}, to="b")],
    )


def _finding(target_id: str = "a", diff: dict | None = None) -> dict:
    return {
        "kind": "structural",
        "question": f"Is {target_id} the process start?",
        "target_element_ids": [target_id],
        "options": [{"label": "Yes, add a start event", "diff": diff}],
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
async def test_analyze_gaps_returns_empty_list_for_clean_response():
    llm = _FakeLLM(_completion(json.dumps({"findings": []})))
    findings = await analyze_gaps(llm, _schema())
    assert findings == []


@pytest.mark.asyncio
async def test_analyze_gaps_parses_a_real_finding():
    llm = _FakeLLM(_completion(json.dumps({"findings": [_finding("a")]})))
    findings = await analyze_gaps(llm, _schema())
    assert len(findings) == 1
    assert findings[0].target_element_ids == ["a"]
    assert findings[0].options[0].label == "Yes, add a start event"


@pytest.mark.asyncio
async def test_analyze_gaps_drops_finding_with_unknown_target_id():
    llm = _FakeLLM(_completion(json.dumps({"findings": [_finding("ghost")]})))
    findings = await analyze_gaps(llm, _schema())
    assert findings == []


@pytest.mark.asyncio
async def test_analyze_gaps_raises_on_invalid_json():
    llm = _FakeLLM(_completion("not json"))
    with pytest.raises(GapAnalysisServiceError):
        await analyze_gaps(llm, _schema())


@pytest.mark.asyncio
async def test_analyze_gaps_raises_on_empty_response_text():
    llm = _FakeLLM(_completion(None, finish_reason="length"))
    with pytest.raises(GapAnalysisServiceError):
        await analyze_gaps(llm, _schema())


@pytest.mark.asyncio
async def test_analyze_gaps_returns_empty_list_for_schema_with_no_elements():
    llm = _FakeLLM(_completion(json.dumps({"findings": [_finding("a")]})))
    empty_schema = ProcessSchema(process_name="P", elements=[], flows=[])
    findings = await analyze_gaps(llm, empty_schema)
    assert findings == []
    assert llm.calls == []  # never even calls the LLM for an empty schema


def _make_process(client) -> str:
    return client.post("/api/processes", json={"name": "P"}).json()["id"]


def _make_process_with_schema(client) -> str:
    process_id = _make_process(client)
    session = get_session_factory()()
    try:
        document = repository.add_document(session, process_id, "doc-seed", "sop.docx", "application/octet-stream", 1)
        session.commit()
        repository.merge_process_schema(session, process_id, _schema(document.id))
        session.commit()
    finally:
        session.close()
    return process_id


@pytest.mark.asyncio
async def test_run_gap_analysis_persists_findings_and_marks_completed(client, fake_llm):
    process_id = _make_process_with_schema(client)
    session = get_session_factory()()
    try:
        fake_llm.complete.return_value = _completion(json.dumps({"findings": [_finding("a")]}))
        findings = await run_gap_analysis(session, fake_llm, process_id)
        session.commit()

        assert len(findings) == 1
        assert findings[0].status == "open"
        process = repository.get_process(session, process_id)
        assert process.gap_analysis_completed_at is not None
    finally:
        session.close()


@pytest.mark.asyncio
async def test_run_gap_analysis_does_not_duplicate_an_existing_finding(client, fake_llm):
    process_id = _make_process_with_schema(client)
    session = get_session_factory()()
    try:
        fake_llm.complete.return_value = _completion(json.dumps({"findings": [_finding("a")]}))
        await run_gap_analysis(session, fake_llm, process_id)
        session.commit()
        await run_gap_analysis(session, fake_llm, process_id)
        session.commit()

        all_findings = repository.list_gap_findings(session, process_id)
        assert len(all_findings) == 1
    finally:
        session.close()


@pytest.mark.asyncio
async def test_run_gap_analysis_never_recreates_a_dismissed_finding(client, fake_llm):
    process_id = _make_process_with_schema(client)
    session = get_session_factory()()
    try:
        fake_llm.complete.return_value = _completion(json.dumps({"findings": [_finding("a")]}))
        [finding] = await run_gap_analysis(session, fake_llm, process_id)
        session.commit()

        finding_model = repository.get_gap_finding(session, process_id, finding.id)
        repository.mark_gap_finding_dismissed(session, finding_model)
        session.commit()

        await run_gap_analysis(session, fake_llm, process_id)
        session.commit()

        all_findings = repository.list_gap_findings(session, process_id)
        assert len(all_findings) == 1
        assert all_findings[0].status == "dismissed"
    finally:
        session.close()
