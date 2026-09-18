def test_send_chat_message_returns_501(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/chat/messages", json={"text": "add a review step"})
    assert r.status_code == 501


def test_list_chat_messages_empty_by_default(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.get(f"/api/processes/{process['id']}/chat/messages")
    assert r.status_code == 200
    assert r.json() == []


def test_apply_chat_message_returns_501(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/chat/messages/msg_fake/apply", json={"confirm": True})
    assert r.status_code == 501


def test_chat_on_missing_process_returns_404(client):
    r = client.post("/api/processes/does-not-exist/chat/messages", json={"text": "hi"})
    assert r.status_code == 404
