import pytest

from app.main import app
from app.registry.base import RegistryConnector, RegistryUnreachableError
from app.registry.manager import get_registries

from .test_api_agents import _automatable_node, _seed_blueprint
from .test_api_blueprint import _finalize
from .test_api_registries import FakeConnector


def _make_artifact(client) -> tuple[dict, dict]:
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact").json()
    return process, artifact


@pytest.fixture
def override_registries():
    def _set(connectors: dict[str, RegistryConnector]):
        app.dependency_overrides[get_registries] = lambda: connectors

    yield _set
    app.dependency_overrides.pop(get_registries, None)


def test_publish_status_before_any_publish_is_generated(client):
    process, artifact = _make_artifact(client)

    r = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish-status")
    assert r.status_code == 200
    body = r.json()
    assert body["lifecycle_status"] == "generated"
    assert body["needs_republish"] is False
    assert body["latest_publication"] is None
    assert body["publications"] == []


def test_publish_creates_a_version_one_publication(client):
    process, artifact = _make_artifact(client)

    r = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "local"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["registry_name"] == "local"
    assert body["version"] == 1
    assert body["status"] == "published"
    assert body["published_by_name"] == "test-editor"
    assert body["agent_artifact_id"] == artifact["id"]

    status = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish-status").json()
    assert status["lifecycle_status"] == "published"
    assert status["needs_republish"] is False
    assert status["latest_publication"]["id"] == body["id"]
    assert len(status["publications"]) == 1


def test_republish_after_regenerate_creates_a_new_version_not_an_overwrite(client):
    process, artifact = _make_artifact(client)
    client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "local"}
    )

    status = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish-status").json()
    assert status["needs_republish"] is False

    # Regenerate the same artifact -- its generated_at moves forward, so the
    # existing publication is now "behind" the current definition (US15.4).
    client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")
    status_after_regen = client.get(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish-status"
    ).json()
    assert status_after_regen["needs_republish"] is True

    second = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "local"}
    ).json()
    assert second["version"] == 2

    final_status = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish-status").json()
    assert final_status["needs_republish"] is False
    assert len(final_status["publications"]) == 2
    # Newest first.
    assert final_status["publications"][0]["version"] == 2
    assert final_status["publications"][1]["version"] == 1


def test_mark_publication_deployed(client):
    process, artifact = _make_artifact(client)
    publication = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "local"}
    ).json()

    r = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publications/{publication['id']}/mark-deployed"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "deployed"
    assert body["deployed_by_name"] == "test-editor"

    status = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish-status").json()
    assert status["lifecycle_status"] == "deployed"


def test_deployed_status_survives_a_later_non_deployed_republish(client):
    """US15.2's lifecycle_status is monotonic across an artifact's whole
    publish history -- marking v1 deployed then publishing v2 shouldn't
    silently regress the artifact back to just 'published'."""
    process, artifact = _make_artifact(client)
    first = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "local"}
    ).json()
    client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publications/{first['id']}/mark-deployed"
    )

    client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")
    client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "local"}
    )

    status = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish-status").json()
    assert status["lifecycle_status"] == "deployed"


def test_publish_requires_editor(client, viewer_client):
    process, artifact = _make_artifact(client)

    r = viewer_client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "local"}
    )
    assert r.status_code == 403

    r = viewer_client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish-status")
    assert r.status_code == 200


def test_mark_deployed_requires_editor(client, viewer_client):
    process, artifact = _make_artifact(client)
    publication = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "local"}
    ).json()

    r = viewer_client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publications/{publication['id']}/mark-deployed"
    )
    assert r.status_code == 403


def test_publish_to_unknown_registry_returns_404(client):
    process, artifact = _make_artifact(client)

    r = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish",
        json={"registry_name": "does-not-exist"},
    )
    assert r.status_code == 404


def test_publish_failure_leaves_status_unchanged(client, override_registries):
    """US15.5: a failed publish (registry unreachable) must not be
    misreported as published, and must leave prior tracked state alone."""
    process, artifact = _make_artifact(client)
    override_registries({"down": FakeConnector("down", push_error=RegistryUnreachableError("timed out"))})

    r = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "down"}
    )
    assert r.status_code == 502
    assert "unreachable" in r.json()["detail"].lower()

    status = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish-status").json()
    assert status["lifecycle_status"] == "generated"
    assert status["publications"] == []


def test_publish_status_for_unknown_artifact_returns_404(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _finalize(client, process["id"])

    r = client.get(f"/api/processes/{process['id']}/agent-artifacts/does-not-exist/publish-status")
    assert r.status_code == 404


def test_publish_tags_the_registry_entry_with_the_node_step_type(client):
    process, artifact = _make_artifact(client)
    client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/publish", json={"registry_name": "local"}
    )

    found = client.get("/api/registries/search", params={"q": "a Agent"}).json()
    assert found["entries"][0]["tags"] == ["data_retrieval_transformation"]
