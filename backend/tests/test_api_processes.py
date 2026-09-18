def test_create_list_get_delete_process(client):
    r = client.post("/api/processes", json={"name": "Onboarding"})
    assert r.status_code == 201
    process = r.json()
    assert process["name"] == "Onboarding"
    assert process["document_count"] == 0
    assert process["has_draft_bpmn"] is False

    r = client.get("/api/processes")
    assert r.status_code == 200
    assert any(p["id"] == process["id"] for p in r.json())

    r = client.get(f"/api/processes/{process['id']}")
    assert r.status_code == 200
    assert r.json()["process_schema"] is None

    r = client.delete(f"/api/processes/{process['id']}")
    assert r.status_code == 204

    r = client.get(f"/api/processes/{process['id']}")
    assert r.status_code == 404


def test_get_missing_process_returns_404(client):
    r = client.get("/api/processes/does-not-exist")
    assert r.status_code == 404
