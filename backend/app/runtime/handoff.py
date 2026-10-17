"""Epic 39, US39.18: a certified hand-off to the customer's own agent
platform.

An approver hands a pending deployment off instead of activating it: it
becomes `external` -- it runs nowhere here, and it has no bindings or
credentials. Its bundle carries what the other platform needs and what
lets it trust the twin's results:

- the agent as an **A2A agent card** (one skill; the input and output
  schemas under an `axyntro` extension) and its tools as **MCP tool
  definitions** (with read-only / destructive hints from the contracts);
- the definition, tool contracts, guardrails and escalation policy, and
  the pinned snapshot (approval, context pack, the tool schemas the twin
  tested);
- an **evidence summary** -- who approved it and when, the twin's
  baseline (pass rate with its interval, decision mix) and the SHA-256 of
  everything above -- signed as a JWS with the platform's Ed25519 key.
  The public key is at `/runtime/v1/handoff/keys` (JWKS), so the receiver
  checks the signature and recomputes the hash without sharing a secret.

The receiving platform reports its executions back
(`POST /runtime/v1/deployments/{id}/reports`, with a runtime client of
the tenant). They're stored as executions of the external deployment, so
the Runtime page's metrics, alerts and drift check (US39.15) cover them
like any other.

The signing key is a PEM file (`HANDOFF_SIGNING_KEY_FILE`). Outside
production it's created on first use; in production it must exist.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import audit
from app.config import get_settings
from app.db.models import AgentDeploymentModel, AgentExecutionModel, ExecutionStepModel, ProcessModel, UserModel
from app.ids import new_id, utcnow
from app.schemas.agents import AgentDefinition
from app.schemas.run import TERMINAL_RUN_STATUSES

from . import drift, monitoring
from .adapters import is_mutating
from .deployments import DeploymentError, check_segregation

FORMAT = "axyntro.agent-handoff/v1"
JWS_TYPE = "axyntro-evidence+jwt"
MAX_REPORTS = 500
_lock = threading.Lock()
_key: Ed25519PrivateKey | None = None


# ---- the signing key ---------------------------------------------------------------------


def _load_or_create(path: Path) -> Ed25519PrivateKey:
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise RuntimeError(f"{path} isn't an Ed25519 private key")
        return key
    if get_settings().environment == "production":
        raise RuntimeError(f"HANDOFF_SIGNING_KEY_FILE ({path}) doesn't exist; production needs a provisioned key")
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    return key


def signing_key() -> Ed25519PrivateKey:
    global _key
    with _lock:
        if _key is None:
            _key = _load_or_create(Path(get_settings().handoff_signing_key_file))
        return _key


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _public_raw() -> bytes:
    return signing_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id() -> str:
    return hashlib.sha256(_public_raw()).hexdigest()[:16]


def jwks() -> dict[str, Any]:
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": _b64(_public_raw()), "kid": key_id(), "use": "sig",
                      "alg": "EdDSA"}]}


# ---- the bundle ------------------------------------------------------------------------------


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _mcp_tools(deployment: AgentDeploymentModel, definition: AgentDefinition) -> list[dict[str, Any]]:
    contracts = {c.system_name: c for c in definition.tool_contracts}
    tools = []
    for system, schema in sorted(deployment.snapshot["tool_schemas"].items()):
        changes = is_mutating(contracts[system]) if system in contracts else True
        tools.append({"name": schema["tool_name"], "description": schema["description"],
                      "inputSchema": schema["parameters"],
                      "annotations": {"title": system, "readOnlyHint": not changes, "destructiveHint": changes,
                                      "idempotentHint": changes}})
    return tools


def _agent_card(deployment: AgentDeploymentModel, definition: AgentDefinition, process_name: str) -> dict[str, Any]:
    fields = lambda items: [f.model_dump(mode="json") for f in items]  # noqa: E731
    return {
        "protocolVersion": "0.3.0",
        "name": definition.name,
        "description": definition.purpose,
        "version": str(deployment.artifact_version),
        "url": None,  # where the receiving platform serves it
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [{"id": deployment.group_key, "name": definition.name, "description": definition.purpose,
                    "tags": [process_name, definition.trigger], "inputModes": ["application/json"],
                    "outputModes": ["application/json"]}],
        "axyntro": {"input_schema": fields(definition.input_schema), "output_schema": fields(definition.output_schema),
                    "human_checkpoint": definition.human_checkpoint},
    }


def _content(session: Session, deployment: AgentDeploymentModel) -> dict[str, Any]:
    definition = AgentDefinition.model_validate(deployment.snapshot["definition"])
    process = session.get(ProcessModel, deployment.process_id)
    snapshot = {k: v for k, v in deployment.snapshot.items() if k not in ("baseline", "twin_evidence")}
    return {
        "agent_card": _agent_card(deployment, definition, process.name if process else ""),
        "mcp_tools": _mcp_tools(deployment, definition),
        "definition": snapshot["definition"],
        "tool_contracts": [c.model_dump(mode="json") for c in definition.tool_contracts],
        "guardrails": {"runtime": definition.guardrails.model_dump(mode="json"),
                       "escalation_policy": definition.escalation_policy.model_dump(mode="json"),
                       "policy_guardrails": definition.policy_guardrails},
        "snapshot": snapshot,
    }


def _evidence(deployment: AgentDeploymentModel, digest: str) -> dict[str, Any]:
    base = drift.baseline_of(deployment)
    summary = ({k: base.get(k) for k in ("batch_id", "runs", "pass_rate", "decision_mix", "escalation_rate",
                                         "dataset_id", "dataset_version")}
               if base.get("available") else {"available": False, "reason": base.get("reason")})
    return {
        "iss": get_settings().api_public_url.rstrip("/"),
        "sub": deployment.id,
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "agent": {"artifact_id": deployment.agent_artifact_id, "version": deployment.artifact_version,
                  "approved_by": deployment.snapshot.get("approved_by"),
                  "approved_at": deployment.snapshot.get("approved_at")},
        "handed_off_by": deployment.approved_by,
        "twin_baseline": summary,
        "content_sha256": digest,
    }


def bundle(session: Session, deployment: AgentDeploymentModel) -> dict[str, Any]:
    content = _content(session, deployment)
    digest = hashlib.sha256(canonical(content)).hexdigest()
    evidence = _evidence(deployment, digest)
    signed = jwt.encode(evidence, signing_key(), algorithm="EdDSA", headers={"kid": key_id(), "typ": JWS_TYPE})
    return {"format": FORMAT, **content, "evidence": evidence, "evidence_jws": signed,
            "jwks_url": "/runtime/v1/handoff/keys",
            "reports_url": f"/runtime/v1/deployments/{deployment.id}/reports",
            "verify": "Check evidence_jws (EdDSA) with the key from jwks_url, then recompute content_sha256 as the "
                      "SHA-256 of the canonical JSON (sorted keys, no spaces) of agent_card, mcp_tools, definition, "
                      "tool_contracts, guardrails and snapshot."}


def verify(bundled: dict[str, Any], keys: dict[str, Any]) -> dict[str, Any]:
    """What a receiving platform does; raises ValueError when it fails."""
    header = jwt.get_unverified_header(bundled["evidence_jws"])
    jwk = next((k for k in keys["keys"] if k["kid"] == header.get("kid")), None)
    if jwk is None:
        raise ValueError("Signed with an unknown key")
    claims = jwt.decode(bundled["evidence_jws"], jwt.PyJWK(jwk).key, algorithms=["EdDSA"])
    content = {k: bundled[k] for k in ("agent_card", "mcp_tools", "definition", "tool_contracts", "guardrails",
                                       "snapshot")}
    if hashlib.sha256(canonical(content)).hexdigest() != claims["content_sha256"]:
        raise ValueError("The bundle was changed after it was signed")
    return claims


# ---- handing off -----------------------------------------------------------------------------


def hand_off(session: Session, deployment: AgentDeploymentModel, user: UserModel) -> None:
    if deployment.status != "pending":
        raise DeploymentError(f"only a pending deployment can be handed off; this one is {deployment.status}")
    check_segregation(session, deployment, user)
    deployment.status = "external"
    deployment.approved_by = user.id
    deployment.activated_at = utcnow()
    audit.record(session, actor=user, action="deployment_handed_off", object_type="deployment",
                 object_id=deployment.id, process_id=deployment.process_id,
                 detail={"artifact_id": deployment.agent_artifact_id, "version": deployment.artifact_version,
                         "key_id": key_id()})
    session.flush()


# ---- reports from the other platform ---------------------------------------------------------


def _at(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _check_report(item: dict[str, Any]) -> str | None:
    if not item.get("id"):
        return "each execution needs its id on your platform"
    if item.get("status") not in TERMINAL_RUN_STATUSES:
        return f"status must be one of {', '.join(sorted(TERMINAL_RUN_STATUSES))}"
    return None


def _steps(execution: AgentExecutionModel, tool_calls: list[dict[str, Any]], at: datetime) -> list[ExecutionStepModel]:
    steps = []
    for seq, call in enumerate(tool_calls, start=1):
        kind = "TOOL_FAILED" if call.get("failed") else "TOOL_EXECUTED"
        steps.append(ExecutionStepModel(
            id=new_id("xstep"), execution_id=execution.id, attempt=1, seq=seq, type=kind, target=call.get("system"),
            step={"seq": seq, "type": kind, "target": call.get("system"), "occurred_at": at.isoformat(),
                  "reason": "reported by the receiving platform"},
            occurred_at=at))
    return steps


def _store(session: Session, deployment: AgentDeploymentModel, client_id: str, item: dict[str, Any]) -> None:
    started, completed = _at(item.get("started_at")), _at(item.get("completed_at")) or utcnow()
    tool_calls = [c for c in item.get("tool_calls") or [] if isinstance(c, dict)]
    execution = AgentExecutionModel(
        id=new_id("exe"), deployment_id=deployment.id, process_id=deployment.process_id,
        artifact_version=deployment.artifact_version, identity_id=deployment.identity_id, client_id=client_id,
        trigger="external", idempotency_key=f"external:{item['id']}"[:200], status=item["status"],
        input=item.get("input"), final_output=item.get("final_output"), deviations=[], attempt=1,
        step_count=len(tool_calls), total_cost_usd=item.get("total_cost_usd"),
        total_tokens=int(item.get("total_tokens") or 0), turns_used=0, started_at=started, completed_at=completed,
        reported_outcome=item.get("outcome"),
    )
    session.add(execution)
    session.flush()
    session.add_all(_steps(execution, tool_calls, started or completed))


def record_reports(session: Session, deployment: AgentDeploymentModel, client_id: str,
                   items: list[dict[str, Any]]) -> dict[str, int]:
    """Stores what ran on the other platform; a repeated id is skipped."""
    if deployment.status != "external":
        raise DeploymentError(f"only a handed-off deployment takes reports; this one is {deployment.status}")
    if len(items) > MAX_REPORTS:
        raise DeploymentError(f"send at most {MAX_REPORTS} executions at a time")
    problems = [f"#{i + 1}: {p}" for i, p in enumerate(_check_report(item) for item in items) if p]
    if problems:
        raise DeploymentError(problems)
    known = set(session.scalars(select(AgentExecutionModel.idempotency_key).where(
        AgentExecutionModel.deployment_id == deployment.id, AgentExecutionModel.trigger == "external")))
    fresh = []
    for item in items:
        key = f"external:{item['id']}"[:200]
        if key not in known:
            known.add(key)
            fresh.append(item)
    for item in fresh:
        _store(session, deployment, client_id, item)
    session.flush()
    monitoring.evaluate_alerts(session, deployment)
    monitoring.check_drift(session, deployment)
    return {"accepted": len(fresh), "duplicates": len(items) - len(fresh)}
