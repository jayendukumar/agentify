"""Epics 24/25: the guided Digital Twin test builder. One deterministic
read (the blueprint-derived context) plus four LLM-backed drafting
endpoints -- none of which persist anything: a confirmed situation is
saved through the ordinary scenario endpoint (app/api/twin.py), generated
data/personas through the environment endpoints (app/api/environments.py).
The drafting endpoints are editor-only since each is a paid LLM call."""

from fastapi import APIRouter, HTTPException, status

from app.db import repository
from app.store import NotFoundError
from app.schemas.agents import AgentDefinition
from app.schemas.twin_builder import (
    DescribeSituationRequest,
    GeneratedPersonas,
    GeneratedRecords,
    GenerateDataRequest,
    GeneratePersonasRequest,
    Situation,
    SituationList,
    SuggestSituationsRequest,
    TestBuilderContext,
)
from app.twin import test_builder
from app.twin.errors import TwinServiceError

from .deps import CurrentUserDep, DbDep, EditorDep, LLMDep

router = APIRouter(prefix="/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder", tags=["twin"])


def _context(db, process_id: str, artifact_id: str) -> TestBuilderContext:
    repository.get_process(db, process_id)  # 404s if missing
    artifact = repository.get_agent_artifact(db, process_id, artifact_id)
    try:
        baseline_xml = repository.get_version(db, process_id, artifact.source_baseline_version_id).xml
    except NotFoundError:
        # Gateways are a nice-to-have for suggestions -- the builder still
        # works from the artifact alone if the baseline is gone.
        baseline_xml = None
    return test_builder.build_context(
        artifact_id=artifact.id,
        definition=AgentDefinition.model_validate(artifact.definition),
        node_ids=list(artifact.node_ids),
        baseline_xml=baseline_xml,
    )


def _bad_gateway(exc: TwinServiceError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/context", response_model=TestBuilderContext)
def get_context(process_id: str, artifact_id: str, db: DbDep, user: CurrentUserDep) -> TestBuilderContext:
    del user
    return _context(db, process_id, artifact_id)


@router.post("/suggest", response_model=SituationList)
async def suggest(
    process_id: str, artifact_id: str, body: SuggestSituationsRequest, db: DbDep, llm: LLMDep, user: EditorDep
) -> SituationList:
    del user
    context = _context(db, process_id, artifact_id)
    try:
        situations = await test_builder.suggest_situations(
            llm, context, count=body.count, focus=body.focus, already_covered=body.already_covered
        )
    except TwinServiceError as exc:
        raise _bad_gateway(exc) from exc
    return SituationList(situations=situations)


@router.post("/describe", response_model=Situation)
async def describe(
    process_id: str, artifact_id: str, body: DescribeSituationRequest, db: DbDep, llm: LLMDep, user: EditorDep
) -> Situation:
    del user
    context = _context(db, process_id, artifact_id)
    try:
        return await test_builder.describe_situation(llm, context, body.description)
    except TwinServiceError as exc:
        raise _bad_gateway(exc) from exc


@router.post("/data", response_model=GeneratedRecords)
async def generate_data(
    process_id: str, artifact_id: str, body: GenerateDataRequest, db: DbDep, llm: LLMDep, user: EditorDep
) -> GeneratedRecords:
    del user
    context = _context(db, process_id, artifact_id)
    try:
        records = await test_builder.generate_records(
            llm, context, mode=body.mode, count=body.count, prompt=body.prompt,
            seed_record=body.seed_record, sample_text=body.sample_text,
        )
    except test_builder.TestBuilderInputError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TwinServiceError as exc:
        raise _bad_gateway(exc) from exc
    return GeneratedRecords(records=records)


@router.post("/personas", response_model=GeneratedPersonas)
async def generate_personas(
    process_id: str, artifact_id: str, body: GeneratePersonasRequest, db: DbDep, llm: LLMDep, user: EditorDep
) -> GeneratedPersonas:
    del user
    context = _context(db, process_id, artifact_id)
    if context.human_checkpoint == "none":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="This agent has no human review step, so there are no reviewers to cast"
        )
    try:
        personas = await test_builder.generate_personas(llm, context, count=body.count, prompt=body.prompt)
    except TwinServiceError as exc:
        raise _bad_gateway(exc) from exc
    return GeneratedPersonas(personas=personas)
