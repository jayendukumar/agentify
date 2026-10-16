"""Epic 38, US38.5: proof that tenants can't reach each other.

Tenant A (the test tenant) gets a realistic set of objects through the
API. Tenant B, signed in with every role, then calls every route that
takes an id, with A's ids in the path -- once with all of A's ids, and
once with B's own process and A's other ids, the way a curious user would
try. Every such request must answer 404 (or 422, when FastAPI rejects the
request's body or query before any handler runs): never a success, which would be a
leak, and never a 403, which would confirm the object exists. Afterwards,
every one of A's rows must be exactly as it was.
"""

import re

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.models import NON_TENANT_TABLES, Base
from app.db.session import get_owner_engine
from app.llm.client import get_llm_client
from app.main import app

from .conftest import ALL_ROLES, TEST_TENANT_ID, make_tenant, sign_in
from .test_api_twin import _run_happy_path_scenario
from .test_batches import ROWS, _setup

B_TENANT_ID = "ten_probe_b"
B_HOST = "tenant-b.test"
SCIM_JSON = "application/scim+json"

# Where tenant A's id for each path parameter comes from.
PARAM_SOURCES: dict[str, tuple[str, str]] = {
    "process_id": ("processes", "id"),
    "artifact_id": ("agent_artifacts", "id"),
    "dataset_id": ("labelled_datasets", "id"),
    "user_id": ("users", "id"),
    "run_id": ("simulation_runs", "id"),
    "entity_id": ("context_entities", "id"),
    "application_id": ("context_entities", "id"),
    "batch_id": ("batch_runs", "id"),
    "scenario_id": ("twin_scenarios", "id"),
    "version_id": ("versions", "id"),
    "group_id": ("directory_groups", "id"),
    "document_id": ("documents", "id"),
    "connector_id": ("connectors", "id"),
    "row_key": ("dataset_rows", "row_key"),
    "rubric_id": ("judge_rubrics", "id"),
    "session_id": ("sessions", "id"),
    "finding_id": ("gap_findings", "id"),
    "environment_id": ("twin_environments", "id"),
    "suite_id": ("regression_suites", "id"),
    "suite_run_id": ("regression_suite_runs", "id"),
    "pack_id": ("context_packs", "id"),
    "token_id": ("scim_tokens", "id"),
    "message_id": ("chat_messages", "id"),
    "publication_id": ("agent_publications", "id"),
    "rule_id": ("decision_rules", "id"),
    "relation_id": ("metamorphic_relations", "id"),
    "schedule_id": ("batch_schedules", "id"),
}
# Parameters that name something every tenant has, not a tenant's object.
# A system name is free text within a process, like a node id.
SHARED_PARAMS = {"node_id": "a", "system_name": "CRM API", "registry_name": "local", "schema_id": "urn:ietf:params:scim:schemas:core:2.0:User", "type_id": "User"}


def _tenant_a_ids() -> dict[str, str]:
    ids = {}
    with get_owner_engine().connect() as conn:
        for param, (table, column) in PARAM_SOURCES.items():
            value = conn.scalar(
                text(f'SELECT "{column}" FROM "{table}" WHERE tenant_id = :t LIMIT 1'), {"t": TEST_TENANT_ID}
            )
            if value is not None:
                ids[param] = value
    return ids


def _tenant_a_digest() -> dict[str, str]:
    """Every row of tenant A, per table, as a hash."""
    digest = {}
    with get_owner_engine().connect() as conn:
        for table in sorted(set(Base.metadata.tables) - NON_TENANT_TABLES):
            digest[table] = conn.scalar(
                text(f'SELECT md5(coalesce(string_agg(t::text, \'|\' ORDER BY t::text), \'\')) FROM "{table}" t WHERE tenant_id = :t'),
                {"t": TEST_TENANT_ID},
            )
    return digest


def _build_tenant_a(client, fake_llm) -> None:
    process, artifact, dataset, scenario = _setup(client, fake_llm, ROWS)
    fake_llm.complete.side_effect = None
    run = _run_happy_path_scenario(client, fake_llm, process, artifact, scenario)
    assert run["status"], run
    base = f"/api/processes/{process['id']}"
    for path, body in [
        (f"{base}/scenarios/{scenario['id']}/batches", {"tier": "smoke"}),
        (f"{base}/batch-schedules", {"scenario_id": scenario["id"], "tier": "smoke"}),
        (f"{base}/judge-rubrics", {"name": "Clear refusal", "field": "explanation", "rubric": "Names the rule.", "min_samples": 5, "min_agreement": 0.8}),
        (f"{base}/datasets/{dataset['id']}/relations", {"name": "Casing", "kind": "invariance", "field": "record_id", "perturbation": {"type": "uppercase"}}),
        (f"{base}/twin-environments", {"name": "Twin", "systems": ["CRM API"], "datasets": {}, "personas": {}, "events": [], "faults": {}}),
        (f"{base}/regression-suites", {"name": "Happy paths", "kind": "twin", "agent_artifact_id": artifact["id"], "scenario_ids": [scenario["id"]]}),
    ]:
        assert client.post(path, json=body).status_code in (200, 201, 202), path
    client.post(f"{base}/documents", files=[("files", ("sop.pdf", b"%PDF-1.4 fake content", "application/pdf"))])
    client.post("/api/organization/entities", json={"kind": "business_unit", "name": "Retail Banking"})
    client.post("/api/organization/connectors", json={"name": "policy-library", "type": "folder", "config": {"path": "."}})
    token = client.post("/api/directory/scim/tokens", json={"label": "Entra ID"}).json()["token"]
    scim = TestClient(app, headers={"Authorization": f"Bearer {token}", "Content-Type": SCIM_JSON})
    group = scim.post("/scim/v2/Groups", json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "displayName": "Analysts"})
    assert group.status_code == 201, group.text


def _probe_routes():
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute):
            continue
        params = re.findall(r"{(\w+)(?::\w+)?}", route.path)
        if not any(p in PARAM_SOURCES for p in params):
            continue  # no tenant object named in the path
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path, params


def _fill(path: str, values: dict[str, str]) -> str:
    return re.sub(r"{(\w+)(?::\w+)?}", lambda m: values[m.group(1)], path)


@pytest.fixture
def tenant_b(fake_llm):
    make_tenant(B_TENANT_ID, "tenant-b", [B_HOST])
    app.dependency_overrides[get_llm_client] = lambda: fake_llm
    b = TestClient(app, base_url=f"http://{B_HOST}")
    assert sign_in(b, "b-admin", ALL_ROLES).status_code == 200
    token = b.post("/api/directory/scim/tokens", json={"label": "B's IdP"}).json()["token"]
    b_scim = TestClient(app, base_url=f"http://{B_HOST}", headers={"Authorization": f"Bearer {token}", "Content-Type": SCIM_JSON})
    yield b, b_scim
    app.dependency_overrides.pop(get_llm_client, None)


def test_no_route_reaches_another_tenants_objects(client, fake_llm, tenant_b):
    _build_tenant_a(client, fake_llm)
    a_ids = _tenant_a_ids()
    # The objects most routes hang off must be real, or the probe proves little.
    for required in ("process_id", "artifact_id", "dataset_id", "user_id", "run_id", "scenario_id", "version_id",
                     "entity_id", "connector_id", "group_id", "session_id", "token_id", "row_key"):
        assert required in a_ids, f"tenant A has no {required} to probe with"
    b, b_scim = tenant_b
    b_process = b.post("/api/processes", json={"name": "B's own"}).json()["id"]
    before = _tenant_a_digest()

    leaks, probed, real_ids, handled = [], 0, 0, 0
    for method, path, params in _probe_routes():
        caller = b_scim if path.startswith("/scim/") else b
        variants = [{**SHARED_PARAMS, **{p: a_ids.get(p, f"missing_{p}") for p in params if p in PARAM_SOURCES}}]
        if "process_id" in params and len(set(params) & set(PARAM_SOURCES)) > 1:
            variants.append({**variants[0], "process_id": b_process})  # B's own process, A's child ids
        for values in variants:
            url = _fill(path, values)
            kwargs = {} if method in ("GET", "DELETE") else {"json": {}}
            response = caller.request(method, url, **kwargs)
            probed += 1
            real_ids += all(p in a_ids for p in params if p in PARAM_SOURCES)
            if response.status_code not in (404, 422):
                leaks.append(f"{method} {path} -> {response.status_code}: {response.text[:150]}")
            handled += response.status_code == 404

    assert leaks == [], "\n".join(leaks)
    assert probed > 200, f"only {probed} requests -- is the route walk still seeing every router?"
    missing = sorted(set(PARAM_SOURCES) - set(a_ids))
    assert real_ids / probed > 0.8, f"only {real_ids} of {probed} probes used real ids of tenant A; none for {missing}"
    assert handled / probed > 0.6, f"only {handled} of {probed} probes reached a handler (the rest were 422s)"
    assert _tenant_a_digest() == before, "tenant B's requests changed tenant A's data"


def test_a_document_file_cannot_be_fetched_from_another_tenant(client, tenant_b):
    process = client.post("/api/processes", json={"name": "P"}).json()
    upload = client.post(
        f"/api/processes/{process['id']}/documents",
        files=[("files", ("sop.pdf", b"%PDF-1.4 secret onboarding notes", "application/pdf"))],
    )
    documents = client.get(f"/api/processes/{process['id']}/documents").json()
    assert upload.status_code in (200, 201, 202) and documents, upload.text
    b, _ = tenant_b
    for path in (
        f"/api/processes/{process['id']}/documents/{documents[0]['id']}",
        f"/api/processes/{process['id']}/documents/{documents[0]['id']}/file",
    ):
        assert b.get(path).status_code == 404
