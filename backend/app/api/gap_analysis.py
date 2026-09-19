import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.bpmn.chat_ops import DiagramApplyRegressionError, DiagramDiffError, apply_diff_and_persist
from app.db import repository
from app.gap_analysis.service import GapAnalysisServiceError, run_gap_analysis
from app.schemas.chat import DiagramDiff
from app.schemas.gap_analysis import GapFinding, GapFindingResolveRequest

from .deps import CurrentUserDep, DbDep, EditorDep, LLMDep

logger = logging.getLogger("app.api.gap_analysis")

router = APIRouter(prefix="/api/processes/{process_id}/gap-findings", tags=["gap-analysis"])


@router.post("/analyze", response_model=list[GapFinding])
async def analyze(process_id: str, db: DbDep, llm: LLMDep, user: EditorDep) -> list[GapFinding]:
    del user
    repository.get_process(db, process_id)  # 404s if missing
    try:
        findings = await run_gap_analysis(db, llm, process_id)
    except GapAnalysisServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return findings


@router.get("", response_model=list[GapFinding])
def list_findings(
    process_id: str, db: DbDep, user: CurrentUserDep, status_filter: str | None = Query(None, alias="status")
) -> list[GapFinding]:
    del user
    return repository.list_gap_findings(db, process_id, status=status_filter)


@router.post("/{finding_id}/resolve", response_model=GapFinding)
async def resolve_finding(
    process_id: str, finding_id: str, body: GapFindingResolveRequest, db: DbDep, llm: LLMDep, user: EditorDep
) -> GapFinding:
    finding = repository.get_gap_finding(db, process_id, finding_id)
    if finding.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This gap finding has already been decided")
    if not (0 <= body.option_index < len(finding.options)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"No option at index {body.option_index}")

    option = finding.options[body.option_index]
    diff = option.get("diff")

    if diff is not None:
        try:
            apply_diff_and_persist(db, process_id, DiagramDiff.model_validate(diff))
        except DiagramApplyRegressionError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except DiagramDiffError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Could not apply this resolution: {exc}") from exc

    result = repository.mark_gap_finding_resolved(db, finding, option_label=option["label"], decided_by=user.id)
    db.commit()

    if diff is not None:
        # Epic 11, US11.5: the schema just changed, so re-run gap analysis
        # -- best-effort, must not fail an otherwise-successful resolution.
        try:
            await run_gap_analysis(db, llm, process_id)
            db.commit()
        except Exception as gap_exc:
            db.rollback()
            logger.warning("gap_analysis_failed", extra={"process_id": process_id, "error": str(gap_exc)})

    return result


@router.post("/{finding_id}/dismiss", response_model=GapFinding)
def dismiss_finding(process_id: str, finding_id: str, db: DbDep, user: EditorDep) -> GapFinding:
    finding = repository.get_gap_finding(db, process_id, finding_id)
    if finding.status != "open":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This gap finding has already been decided")
    return repository.mark_gap_finding_dismissed(db, finding, decided_by=user.id)
