"""Epic 35: results at volume and the statistical release gate."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.batches import results
from app.batches.service import get_batch
from app.db import repository

from .deps import CurrentUserDep, DbDep, EditorDep

router = APIRouter(prefix="/api/processes/{process_id}", tags=["results"])


@router.get("/batches/{batch_id}/results", response_model=results.BatchResults)
def batch_results(process_id: str, batch_id: str, db: DbDep, user: CurrentUserDep) -> results.BatchResults:
    """US35.1-US35.5, US35.8 -- partial while the batch is still running."""
    del user
    return results.batch_results(db, get_batch(db, process_id, batch_id))


@router.get("/batch-comparison", response_model=results.Comparison)
def compare_batches(process_id: str, a: str, b: str, db: DbDep, user: CurrentUserDep) -> results.Comparison:
    """US35.6: paired on the cases both batches ran."""
    del user
    try:
        return results.compare(db, get_batch(db, process_id, a), get_batch(db, process_id, b))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/batches/{batch_id}/evidence")
def batch_evidence(process_id: str, batch_id: str, db: DbDep, user: CurrentUserDep) -> dict:
    """US35.9."""
    batch = get_batch(db, process_id, batch_id)
    artifact = repository.get_agent_artifact(db, process_id, batch.agent_artifact_id)
    criteria = results.ReleaseCriteria.model_validate(artifact.release_criteria) if artifact.release_criteria else None
    from app.context.audit import record

    record(db, actor_type="user", actor=user.id, action="export_batch_evidence", process_id=process_id, detail=batch_id)
    return results.evidence_record(db, batch, criteria)


@router.get("/agent-artifacts/{artifact_id}/release-criteria", response_model=results.ReleaseCriteria | None)
def get_criteria(process_id: str, artifact_id: str, db: DbDep, user: CurrentUserDep) -> results.ReleaseCriteria | None:
    del user
    artifact = repository.get_agent_artifact(db, process_id, artifact_id)
    return results.ReleaseCriteria.model_validate(artifact.release_criteria) if artifact.release_criteria else None


@router.put("/agent-artifacts/{artifact_id}/release-criteria", response_model=results.ReleaseCriteria)
def set_criteria(process_id: str, artifact_id: str, body: results.ReleaseCriteria, db: DbDep, user: EditorDep) -> results.ReleaseCriteria:
    """US35.7: what "good enough to release" means for this agent."""
    del user
    artifact = repository.get_agent_artifact(db, process_id, artifact_id)
    artifact.release_criteria = body.model_dump()
    db.flush()
    return body


@router.delete("/agent-artifacts/{artifact_id}/release-criteria", status_code=status.HTTP_204_NO_CONTENT)
def clear_criteria(process_id: str, artifact_id: str, db: DbDep, user: EditorDep) -> None:
    del user
    repository.get_agent_artifact(db, process_id, artifact_id).release_criteria = None
    db.flush()


@router.get("/agent-artifacts/{artifact_id}/release-gate", response_model=results.GateResult)
def release_gate(process_id: str, artifact_id: str, db: DbDep, user: CurrentUserDep) -> results.GateResult:
    del user
    artifact = repository.get_agent_artifact(db, process_id, artifact_id)
    criteria = results.ReleaseCriteria.model_validate(artifact.release_criteria) if artifact.release_criteria else results.ReleaseCriteria()
    return results.gate(db, artifact, criteria)
