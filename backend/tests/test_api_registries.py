from datetime import datetime, timezone

import pytest

from app.main import app
from app.registry.base import (
    RegistryAuthError,
    RegistryConnector,
    RegistryEntry,
    RegistryHealth,
    RegistryUnreachableError,
)
from app.registry.manager import get_registries


class FakeConnector(RegistryConnector):
    """A controllable connector for exercising the unreachable/auth-error
    paths (US13.6) that the real LocalRegistryConnector can't simulate --
    same role as conftest.py's FakeLLMClient for LLM calls."""

    def __init__(self, name: str, *, health: RegistryHealth | None = None, search_error=None, push_error=None):
        super().__init__(name, "fake")
        self._health = health or RegistryHealth(name=name, reachable=True, authenticated=True)
        self._search_error = search_error
        self._push_error = push_error
        self.pushed: list[RegistryEntry] = []

    def check_health(self) -> RegistryHealth:
        return self._health

    def push(self, *, agent_name, definition, tags=None, source_process_id=None, source_node_ids=None, pushed_by=None):
        if self._push_error:
            raise self._push_error
        entry = RegistryEntry(
            id=f"{self.name}-{len(self.pushed) + 1}",
            registry_name=self.name,
            agent_name=agent_name,
            tags=tags or [],
            definition=definition,
            pushed_at=datetime.now(timezone.utc),
            source_process_id=source_process_id,
            source_node_ids=source_node_ids or [],
            pushed_by=pushed_by,
        )
        self.pushed.append(entry)
        return entry

    def pull(self, entry_id: str):
        return next((e for e in self.pushed if e.id == entry_id), None)

    def search(self, query: str):
        if self._search_error:
            raise self._search_error
        needle = query.strip().lower()
        if not needle:
            return list(self.pushed)
        return [e for e in self.pushed if needle in e.agent_name.lower()]


@pytest.fixture
def override_registries():
    def _set(connectors: dict[str, RegistryConnector]):
        app.dependency_overrides[get_registries] = lambda: connectors

    yield _set
    app.dependency_overrides.pop(get_registries, None)


def test_list_registries_reports_local_registry_health(client):
    r = client.get("/api/registries")
    assert r.status_code == 200
    names = {row["name"]: row for row in r.json()}
    assert "local" in names
    assert names["local"]["type"] == "local"
    assert names["local"]["reachable"] is True
    assert names["local"]["authenticated"] is True


def test_push_requires_editor(viewer_client):
    r = viewer_client.post(
        "/api/registries/local/push", json={"agent_name": "Invoice Agent", "definition": {"name": "Invoice Agent"}}
    )
    assert r.status_code == 403


def test_push_unknown_registry_returns_404(client):
    r = client.post(
        "/api/registries/does-not-exist/push", json={"agent_name": "Invoice Agent", "definition": {"name": "x"}}
    )
    assert r.status_code == 404


def test_push_and_search_round_trip(client):
    push = client.post(
        "/api/registries/local/push",
        json={
            "agent_name": "Invoice Fetcher Agent",
            "definition": {"name": "Invoice Fetcher Agent", "purpose": "Fetch invoices"},
            "tags": ["finance", "erp"],
        },
    )
    assert push.status_code == 200
    body = push.json()
    assert body["registry_name"] == "local"
    assert body["agent_name"] == "Invoice Fetcher Agent"
    assert body["pushed_by_name"] == "test-editor"

    found = client.get("/api/registries/search", params={"q": "invoice"})
    assert found.status_code == 200
    result = found.json()
    assert any(e["id"] == body["id"] for e in result["entries"])
    assert result["registry_errors"] == {}

    not_found = client.get("/api/registries/search", params={"q": "no-such-agent-anywhere"})
    assert not_found.json()["entries"] == []

    browse_all = client.get("/api/registries/search")
    assert any(e["id"] == body["id"] for e in browse_all.json()["entries"])


def test_search_reports_per_registry_errors_without_dropping_other_results(client, override_registries):
    healthy = FakeConnector("healthy")
    healthy.push(agent_name="Working Agent", definition={"name": "Working Agent"})
    broken = FakeConnector("broken", search_error=RegistryUnreachableError("connection refused"))
    override_registries({"healthy": healthy, "broken": broken})

    r = client.get("/api/registries/search")
    assert r.status_code == 200
    body = r.json()
    assert [e["agent_name"] for e in body["entries"]] == ["Working Agent"]
    assert "connection refused" in body["registry_errors"]["broken"]


def test_push_to_unreachable_registry_returns_502(client, override_registries):
    override_registries({"down": FakeConnector("down", push_error=RegistryUnreachableError("timed out"))})

    r = client.post("/api/registries/down/push", json={"agent_name": "x", "definition": {"name": "x"}})
    assert r.status_code == 502
    assert "unreachable" in r.json()["detail"].lower()


def test_push_with_bad_credentials_returns_502(client, override_registries):
    override_registries({"vendor": FakeConnector("vendor", push_error=RegistryAuthError("invalid api key"))})

    r = client.post("/api/registries/vendor/push", json={"agent_name": "x", "definition": {"name": "x"}})
    assert r.status_code == 502
    assert "credentials" in r.json()["detail"].lower()


def test_list_registries_reports_a_registry_reported_unreachable(client, override_registries):
    override_registries(
        {"down": FakeConnector("down", health=RegistryHealth(name="down", reachable=False, authenticated=False, message="no route to host"))}
    )

    r = client.get("/api/registries")
    assert r.status_code == 200
    row = r.json()[0]
    assert row["reachable"] is False
    assert row["message"] == "no route to host"
