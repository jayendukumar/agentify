from app.request_context import REQUEST_ID_HEADER


def test_response_carries_a_request_id_header(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.headers.get(REQUEST_ID_HEADER)


def test_caller_supplied_request_id_is_echoed_back(client):
    r = client.get("/healthz", headers={REQUEST_ID_HEADER: "trace-abc123"})
    assert r.headers.get(REQUEST_ID_HEADER) == "trace-abc123"


def test_two_requests_get_different_request_ids(client):
    r1 = client.get("/healthz")
    r2 = client.get("/healthz")
    assert r1.headers.get(REQUEST_ID_HEADER) != r2.headers.get(REQUEST_ID_HEADER)
