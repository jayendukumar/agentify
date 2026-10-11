"""Epic 31, US31.1: context jobs -- policy-document ingestion runs on the
queue when the worker is enabled (it can take minutes for a large
document)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.jobs.worker import handler


@handler("policy_ingest")
async def policy_ingest(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    from app.connectors.jobs import get_job_llm

    from . import ingestion, repository

    await ingestion.process_policy_document(payload["document_id"], get_job_llm())
    document = repository.get_context_document_model(session, payload["document_id"])
    session.refresh(document)
    if document.status == "failed":
        raise RuntimeError(document.error_message or "policy ingestion failed")
    return {"document_id": document.id, "proposals": document.proposal_count}
