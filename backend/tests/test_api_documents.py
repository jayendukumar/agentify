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
