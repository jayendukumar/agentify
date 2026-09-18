import io
import json

from docx import Document as DocxDocument
from sqlalchemy import select

from app.db.models import DocumentEmbeddingModel, ProcessSchemaChangeModel
from app.db.session import get_session_factory
from app.llm.types import ChatCompletionResult, Usage


def _docx_bytes(paragraphs: list[str]) -> bytes:
    document = DocxDocument()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _llm_result(payload: dict) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=json.dumps(payload),
        tool_calls=[],
        finish_reason="stop",
        usage=Usage(input_tokens=10, output_tokens=10, total_tokens=20),
        model="qwen/qwen3.7-flash",
    )


_PAYLOAD = {
    "actors": [{"id": "actor-1", "name": "Requester", "type": "role"}],
    "elements": [
        {
            "id": "el-1",
            "type": "task",
            "label": "Submit the request form",
            "actor_id": "actor-1",
            "source_refs": [{"document_id": "x", "location": "paragraph 1", "excerpt": "Submit the request form."}],
            "confidence": "high",
        }
    ],
    "flows": [],
}


def test_document_upload_stores_embeddings_and_schema_change_log(client, fake_llm):
    fake_llm.complete.return_value = _llm_result(_PAYLOAD)

    process = client.post("/api/processes", json={"name": "Onboarding"}).json()
    files = [
        (
            "files",
            (
                "sop.docx",
                _docx_bytes(["Step 1: Submit the request form.", "Step 2: Manager reviews it."]),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
    ]
    r = client.post(f"/api/processes/{process['id']}/documents", files=files)
    document_id = r.json()[0]["id"]

    session = get_session_factory()()
    try:
        embeddings = session.scalars(
            select(DocumentEmbeddingModel).where(DocumentEmbeddingModel.document_id == document_id)
        ).all()
        changes = session.scalars(
            select(ProcessSchemaChangeModel).where(ProcessSchemaChangeModel.process_id == process["id"])
        ).all()
    finally:
        session.close()

    # Two non-empty paragraphs went in -- one embedding row per extracted
    # block (US2.3), independent of however many LLM-structured elements
    # came out the other end.
    assert len(embeddings) == 2
    assert len(embeddings[0].embedding) == 384
    assert {e.location for e in embeddings} == {"paragraph 1", "paragraph 2"}

    # US2.6: the merge was logged, not just applied silently.
    assert len(changes) == 1
    assert changes[0].document_id == document_id
    assert "Initial extraction" in changes[0].summary


def test_process_data_survives_a_fresh_session(client, fake_llm):
    """Not a restart (that would need a second process), but proves the
    data isn't coming from the in-memory store -- a brand new DB session,
    independent of the one the request used, sees it."""
    fake_llm.complete.return_value = _llm_result(_PAYLOAD)

    process = client.post("/api/processes", json={"name": "Durable"}).json()
    files = [
        (
            "files",
            ("sop.docx", _docx_bytes(["Step 1: Submit the request form."]), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        )
    ]
    client.post(f"/api/processes/{process['id']}/documents", files=files)

    from app.db import repository

    session = get_session_factory()()
    try:
        fetched = repository.get_process(session, process["id"])
        schema = repository.get_process_schema(session, process["id"])
    finally:
        session.close()

    assert fetched.name == "Durable"
    assert schema is not None
    assert len(schema.elements) == 1
