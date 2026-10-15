"""Live smoke test of Epic 37 step 4 (SCIM provisioning and group
mappings) against the docker compose stack with Keycloak. Keycloak has no
outbound SCIM, so this script plays the IdP's provisioning side: it
replays Entra ID's request sequence over HTTP, then provisions a Keycloak
user over SCIM and checks they're linked at their first real sign-in.

    python testing/keycloak/smoke_scim.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from smoke_sso import API, admin_api, check, me, results, sign_in, user_id  # noqa: E402

FIXTURE = Path(__file__).parents[2] / "backend" / "tests" / "fixtures" / "scim" / "entra_id.json"
SCIM_JSON = "application/scim+json"
PATCH_OP = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


def replay(scim: httpx.Client) -> list[str]:
    """Entra ID's sequence; returns the steps that didn't go as expected."""
    captured: dict[str, str] = {}
    failures = []

    def fill(value):
        text = json.dumps(value)
        for name, captured_value in captured.items():
            text = text.replace("{{" + name + "}}", captured_value)
        return json.loads(text)

    for step in json.loads(FIXTURE.read_text(encoding="utf-8"))["steps"]:
        content = json.dumps(fill(step["body"])) if "body" in step else None
        response = scim.request(step["method"], f"/scim/v2{fill(step['path'])}", content=content)
        if response.status_code != step["status"]:
            # A rerun finds the user from the last run: clean up and go on.
            if step["status"] == 201 and response.status_code == 409 and "user" not in captured:
                old = scim.get("/scim/v2/Users", params={"filter": f'userName eq "{step["body"]["userName"]}"'}).json()
                scim.delete(f"/scim/v2/Users/{old['Resources'][0]['id']}")
                response = scim.request(step["method"], f"/scim/v2{fill(step['path'])}", content=content)
            if response.status_code != step["status"]:
                failures.append(f"{step['name']}: {response.status_code} {response.text[:120]}")
                continue
        for name, field in step.get("capture", {}).items():
            captured[name] = response.json()[field]
    return failures


def provision(scim: httpx.Client, username: str, external_id: str, group: str) -> str:
    found = scim.get("/scim/v2/Users", params={"filter": f'userName eq "{username}@meridian.example"'}).json()
    if found["totalResults"]:
        scim_id = found["Resources"][0]["id"]
        scim.patch(f"/scim/v2/Users/{scim_id}", content=json.dumps(
            {"schemas": [PATCH_OP], "Operations": [{"op": "replace", "value": {"active": True}}]}))
    else:
        scim_id = scim.post("/scim/v2/Users", content=json.dumps({
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": f"{username}@meridian.example",
            "externalId": external_id, "active": True, "name": {"givenName": "Olivia", "familyName": "Brandt"},
            "emails": [{"primary": True, "type": "work", "value": f"{username}@meridian.example"}],
        })).json()["id"]
    groups = scim.get("/scim/v2/Groups", params={"filter": f'displayName eq "{group}"'}).json()
    if groups["totalResults"]:
        group_id = groups["Resources"][0]["id"]
    else:
        group_id = scim.post("/scim/v2/Groups", content=json.dumps(
            {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "displayName": group})).json()["id"]
    scim.patch(f"/scim/v2/Groups/{group_id}", content=json.dumps(
        {"schemas": [PATCH_OP], "Operations": [{"op": "Add", "path": "members", "value": [{"value": scim_id}]}]}))
    return scim_id


def main() -> int:
    sam, _ = sign_in("sam")  # Admin, from the group mappings seeded out of OIDC_GROUP_ROLES
    csrf = me(sam).json()["csrf_token"]
    issued = sam.post(f"{API}/api/directory/scim/tokens", json={"label": "smoke test"}, headers={"X-CSRF-Token": csrf})
    check("an admin generates a SCIM token", issued.status_code == 201)
    scim = httpx.Client(base_url=API, timeout=20, headers={
        "Authorization": f"Bearer {issued.json()['token']}", "Content-Type": SCIM_JSON})
    check("a wrong token is refused", httpx.get(f"{API}/scim/v2/Users", headers={"Authorization": "Bearer x"}).status_code == 401)

    failures = replay(scim)
    check("Entra ID's request sequence over HTTP", not failures, "; ".join(failures))

    # Provision Olivia over SCIM, with externalId = her Keycloak id (= `sub`).
    keycloak = admin_api()
    olivia_kc = user_id(keycloak, "olivia")
    scim_id = provision(scim, "olivia", olivia_kc, "axyntro-process-owners")
    olivia, landed = sign_in("olivia")
    profile = me(olivia).json()
    check("the provisioned user is linked at first sign-in", profile.get("id") == scim_id, landed)
    check("their roles come from the SCIM group",
          [(g["role"], g["source"]) for g in profile.get("roles", [])] == [("process_owner", "group")], str(profile.get("roles")))

    # A mapping change is previewed before it's saved.
    mappings = sam.get(f"{API}/api/directory/group-mappings").json()
    proposal = {"mappings": [*mappings, {"group": "axyntro-process-owners", "role": "auditor"}]}
    preview = sam.post(f"{API}/api/directory/group-mappings/preview", json=proposal, headers={"X-CSRF-Token": csrf}).json()
    olivia_change = next((c for c in preview if c["user_id"] == scim_id), None)
    check("the preview shows who would gain what",
          olivia_change is not None and [g["role"] for g in olivia_change["gained"]] == ["auditor"], str(preview))
    check("previewing changed nothing", [g["role"] for g in me(olivia).json()["roles"]] == ["process_owner"])

    # Deactivated in the directory: the session ends on the next request.
    deactivate = scim.patch(f"/scim/v2/Users/{scim_id}", content=json.dumps(
        {"schemas": [PATCH_OP], "Operations": [{"op": "Replace", "path": "active", "value": "False"}]}))
    check("SCIM deactivation is accepted", deactivate.status_code == 200 and deactivate.json()["active"] is False)
    check("the user's session ended at once", me(olivia).status_code == 401)
    _, landed = sign_in("olivia")
    check("and they can't sign in again", "auth_error=deactivated" in landed, landed)
    scim.patch(f"/scim/v2/Users/{scim_id}", content=json.dumps(
        {"schemas": [PATCH_OP], "Operations": [{"op": "replace", "value": {"active": True}}]}))

    events = sam.get(f"{API}/api/audit-events", params={"limit": 200}).json()
    deactivated = [e for e in events if e["action"] == "user_deactivated" and e["actor_type"] == "service"]
    check("the deactivation is audited as the SCIM client", bool(deactivated) and deactivated[0]["actor_name"] == "SCIM (smoke test)")

    print(f"{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
