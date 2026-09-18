import pytest

# Both are real frontend origins in local dev: Vite is pinned to 127.0.0.1
# (frontend/vite.config.ts, to avoid an IPv6-only binding issue), but
# "localhost" is also legitimate if someone navigates there directly --
# browsers treat the two as distinct origins even though they're the same
# machine, so both must be allowed or requests get silently blocked.
@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:3000"])
def test_frontend_origin_is_allowed(client, origin):
    r = client.options(
        "/api/processes",
        headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
    )
    assert r.headers.get("access-control-allow-origin") == origin


def test_unlisted_origin_is_not_allowed(client):
    r = client.options(
        "/api/processes",
        headers={"Origin": "http://evil.example.com", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
