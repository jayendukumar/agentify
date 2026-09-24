def _docx_bytes(paragraphs: list[str]) -> bytes:
    import io

    from docx import Document as DocxDocument

    document = DocxDocument()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_upload_list_get_document(client):
    process = client.post("/api/processes", json={"name": "Doc Upload Test"}).json()

    files = [("files", ("sop.pdf", b"%PDF-1.4 fake content", "application/pdf"))]
    r = client.post(f"/api/processes/{process['id']}/documents", files=files)
    assert r.status_code == 201
    docs = r.json()
    assert len(docs) == 1
    assert docs[0]["filename"] == "sop.pdf"
    assert docs[0]["status"] == "queued"

    r = client.get(f"/api/processes/{process['id']}/documents")
    assert r.status_code == 200
    assert len(r.json()) == 1

    doc_id = docs[0]["id"]
    r = client.get(f"/api/processes/{process['id']}/documents/{doc_id}")
    assert r.status_code == 200
    assert r.json()["id"] == doc_id


def test_upload_rejects_unsupported_content_type(client):
    process = client.post("/api/processes", json={"name": "Bad Upload"}).json()

    files = [("files", ("virus.exe", b"MZ", "application/x-msdownload"))]
    r = client.post(f"/api/processes/{process['id']}/documents", files=files)
    assert r.status_code == 400


def test_upload_to_missing_process_returns_404(client):
    files = [("files", ("sop.pdf", b"%PDF-1.4", "application/pdf"))]
    r = client.post("/api/processes/does-not-exist/documents", files=files)
    assert r.status_code == 404


def test_editor_can_delete_a_failed_document(client, monkeypatch):
    # No extractable text -> structuring never runs -> document ends up
    # "failed" without ever merging into the process schema, the one case
    # deletion is allowed for.
    monkeypatch.setattr("app.ingestion.pipeline.extract_pdf", lambda data: [])
    process = client.post("/api/processes", json={"name": "Delete Failed Doc"}).json()
    files = [("files", ("blank.pdf", b"%PDF-1.4 minimal", "application/pdf"))]
    document_id = client.post(f"/api/processes/{process['id']}/documents", files=files).json()[0]["id"]
    assert client.get(f"/api/processes/{process['id']}/documents/{document_id}").json()["status"] == "failed"

    r = client.delete(f"/api/processes/{process['id']}/documents/{document_id}")
    assert r.status_code == 204

    assert client.get(f"/api/processes/{process['id']}/documents/{document_id}").status_code == 404
    assert client.get(f"/api/processes/{process['id']}/documents").json() == []


def test_deleting_a_failed_document_also_removes_the_stored_file(client, monkeypatch):
    from app.config import get_settings
    from app.ingestion import storage

    monkeypatch.setattr("app.ingestion.pipeline.extract_pdf", lambda data: [])
    process = client.post("/api/processes", json={"name": "Delete Cleans Storage"}).json()
    files = [("files", ("blank.pdf", b"%PDF-1.4 minimal", "application/pdf"))]
    document_id = client.post(f"/api/processes/{process['id']}/documents", files=files).json()[0]["id"]

    settings = get_settings()
    stored_path = storage._document_path(settings, process["id"], document_id, "blank.pdf")
    assert stored_path.exists()

    r = client.delete(f"/api/processes/{process['id']}/documents/{document_id}")
    assert r.status_code == 204
    assert not stored_path.exists()


def test_cannot_delete_a_successfully_processed_document(client, fake_llm):
    import json

    from app.llm.types import ChatCompletionResult, Usage

    payload = {
        "is_process_definition": True,
        "process_definition_confidence": 90,
        "validation_message": "Clear ordered steps.",
        "actors": [],
        "elements": [],
        "flows": [],
    }
    fake_llm.complete.return_value = ChatCompletionResult(
        text=json.dumps(payload), tool_calls=[], finish_reason="stop",
        usage=Usage(input_tokens=10, output_tokens=10, total_tokens=20), model="qwen/qwen3.7-flash",
    )
    process = client.post("/api/processes", json={"name": "Delete Done Doc"}).json()
    files = [
        (
            "files",
            (
                "sop.docx",
                _docx_bytes(["Submit the request form."]),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
    ]
    document_id = client.post(f"/api/processes/{process['id']}/documents", files=files).json()[0]["id"]
    assert client.get(f"/api/processes/{process['id']}/documents/{document_id}").json()["status"] == "done"

    r = client.delete(f"/api/processes/{process['id']}/documents/{document_id}")
    assert r.status_code == 400

    assert client.get(f"/api/processes/{process['id']}/documents/{document_id}").status_code == 200


def test_viewer_cannot_delete_a_document(client, viewer_client, monkeypatch):
    monkeypatch.setattr("app.ingestion.pipeline.extract_pdf", lambda data: [])
    process = client.post("/api/processes", json={"name": "Viewer Delete Attempt"}).json()
    files = [("files", ("blank.pdf", b"%PDF-1.4 minimal", "application/pdf"))]
    document_id = client.post(f"/api/processes/{process['id']}/documents", files=files).json()[0]["id"]

    r = viewer_client.delete(f"/api/processes/{process['id']}/documents/{document_id}")
    assert r.status_code == 403
