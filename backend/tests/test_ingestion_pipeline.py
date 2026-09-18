import io
import json
import zipfile

from docx import Document as DocxDocument

from app.llm.types import ChatCompletionResult, Usage

_VSDX_PAGE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">
  <Shapes>
    <Shape ID="1"><Text>Submit request</Text></Shape>
    <Shape ID="2"><Text>Manager approves</Text></Shape>
    <Shape ID="3"></Shape>
  </Shapes>
  <Connects>
    <Connect FromSheet="3" FromCell="BeginX" ToSheet="1" ToCell="PinX"/>
    <Connect FromSheet="3" FromCell="EndX" ToSheet="2" ToCell="PinX"/>
  </Connects>
</PageContents>
"""


def _vsdx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("visio/pages/page1.xml", _VSDX_PAGE_XML)
    return buffer.getvalue()


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


_VALID_PAYLOAD = {
    "actors": [{"id": "actor-1", "name": "Requester", "type": "role"}],
    "elements": [
        {
            "id": "el-1",
            "type": "task",
            "label": "Submit the request form",
            "actor_id": "actor-1",
            "inputs": [],
            "outputs": ["request form"],
            "systems_touched": [],
            "source_refs": [{"document_id": "will-be-overwritten", "location": "paragraph 1", "excerpt": "Submit the request form."}],
            "confidence": "high",
        }
    ],
    "flows": [],
}


def test_docx_upload_processes_in_background_and_updates_status(client, fake_llm):
    fake_llm.complete.return_value = _llm_result(_VALID_PAYLOAD)

    process = client.post("/api/processes", json={"name": "Onboarding"}).json()
    files = [
        (
            "files",
            (
                "sop.docx",
                _docx_bytes(["Step 1: Submit the request form."]),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
    ]

    r = client.post(f"/api/processes/{process['id']}/documents", files=files)
    assert r.status_code == 201
    document_id = r.json()[0]["id"]

    # TestClient runs BackgroundTasks to completion before the request
    # context manager returns control here, so the document is already
    # processed by this point -- no polling needed.
    r = client.get(f"/api/processes/{process['id']}/documents/{document_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["error_message"] is None

    r = client.get(f"/api/processes/{process['id']}")
    schema = r.json()["process_schema"]
    assert schema is not None
    assert schema["process_name"] == "Onboarding"
    assert len(schema["elements"]) == 1
    assert schema["elements"][0]["label"] == "Submit the request form"
    assert schema["elements"][0]["source_refs"][0]["document_id"] == document_id

    fake_llm.complete.assert_awaited_once()
    _, kwargs = fake_llm.complete.await_args
    assert kwargs["operation"] == "document_extraction"


def test_pdf_upload_with_no_extractable_text_marks_document_failed(client, fake_llm, monkeypatch):
    # Force the deterministic extractor to find nothing, so structuring
    # never even calls the LLM -- exercises the US1.7 failure path.
    monkeypatch.setattr("app.ingestion.pipeline.extract_pdf", lambda data: [])

    process = client.post("/api/processes", json={"name": "Empty"}).json()
    files = [("files", ("blank.pdf", b"%PDF-1.4 minimal", "application/pdf"))]

    r = client.post(f"/api/processes/{process['id']}/documents", files=files)
    document_id = r.json()[0]["id"]

    r = client.get(f"/api/processes/{process['id']}/documents/{document_id}")
    assert r.json()["status"] == "failed"
    assert "No extractable text" in r.json()["error_message"]
    fake_llm.complete.assert_not_awaited()


def test_vsdx_upload_processes_in_background_and_updates_status(client, fake_llm):
    fake_llm.complete.return_value = _llm_result(_VALID_PAYLOAD)

    process = client.post("/api/processes", json={"name": "Diagram Only"}).json()
    files = [("files", ("flow.vsdx", _vsdx_bytes(), "application/vnd.ms-visio.drawing.main+xml"))]

    r = client.post(f"/api/processes/{process['id']}/documents", files=files)
    document_id = r.json()[0]["id"]

    r = client.get(f"/api/processes/{process['id']}/documents/{document_id}")
    assert r.json()["status"] == "done"
    fake_llm.complete.assert_awaited_once()

    # The LLM call should have received the deterministically-derived shape
    # text and connector relationship, not raw bytes -- confirms extract_vsdx
    # actually ran and fed structure_process, not a stub/no-op path.
    args, _ = fake_llm.complete.await_args
    prompt_text = args[0][0].content
    assert "Submit request" in prompt_text
    assert "Manager approves" in prompt_text
    assert "Connector:" in prompt_text


def test_image_upload_processes_via_vision_and_updates_status(client, fake_llm):
    fake_llm.complete.return_value = _llm_result(_VALID_PAYLOAD)

    process = client.post("/api/processes", json={"name": "Whiteboard Photo"}).json()
    files = [("files", ("flow.png", b"fake-png-bytes", "image/png"))]

    r = client.post(f"/api/processes/{process['id']}/documents", files=files)
    document_id = r.json()[0]["id"]

    r = client.get(f"/api/processes/{process['id']}/documents/{document_id}")
    assert r.json()["status"] == "done"
    fake_llm.complete.assert_awaited_once()

    # No deterministic extraction step exists for images -- confirm the
    # message actually carries an image content block, not just text.
    args, _ = fake_llm.complete.await_args
    content_blocks = args[0][0].content
    assert isinstance(content_blocks, list)
    assert {block.type for block in content_blocks} == {"text", "image_url"}


def test_second_document_merges_into_existing_process_schema(client, fake_llm):
    process = client.post("/api/processes", json={"name": "Multi-Doc"}).json()

    fake_llm.complete.return_value = _llm_result(_VALID_PAYLOAD)
    files_1 = [("files", ("sop1.docx", _docx_bytes(["Step 1: Submit the request form."]), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    client.post(f"/api/processes/{process['id']}/documents", files=files_1)

    second_payload = {
        "actors": [{"id": "actor-2", "name": "Manager", "type": "role"}],
        "elements": [
            {
                "id": "el-2",
                "type": "task",
                "label": "Approve the request",
                "actor_id": "actor-2",
                "inputs": [],
                "outputs": [],
                "systems_touched": [],
                "source_refs": [{"document_id": "x", "location": "paragraph 1", "excerpt": "Approve the request."}],
                "confidence": "medium",
            }
        ],
        "flows": [],
    }
    fake_llm.complete.return_value = _llm_result(second_payload)
    files_2 = [("files", ("sop2.docx", _docx_bytes(["Manager approves the request."]), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]
    client.post(f"/api/processes/{process['id']}/documents", files=files_2)

    r = client.get(f"/api/processes/{process['id']}")
    elements = r.json()["process_schema"]["elements"]
    assert {e["label"] for e in elements} == {"Submit the request form", "Approve the request"}
