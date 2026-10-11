"""Epic 33 on the Epic 31 job queue: label-first generation can take many
LLM calls, so it runs as a job when the worker is enabled."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.jobs.worker import handler


@handler("label_first_generate")
async def label_first_generate(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    from app.connectors.jobs import get_job_llm
    from app.datasets import service as datasets
    from app.llm.routing import llm_route_for

    from . import service

    dataset = datasets.get_dataset_model(session, payload["process_id"], payload["dataset_id"])
    with llm_route_for(session, process_id=payload["process_id"]):
        result = await service.generate_label_first(
            session, get_job_llm(), dataset, service.GenerateRequest.model_validate(payload["request"]), user_id=payload.get("user_id")
        )
    return result.model_dump()
