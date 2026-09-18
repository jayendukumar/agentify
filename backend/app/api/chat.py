from fastapi import APIRouter, HTTPException, status

from app.bpmn.builder import build_bpmn_xml
from app.bpmn.chat_ops import DiagramDiffError, apply_diagram_diff, humanize_validation_issues
from app.bpmn.layout import extract_node_positions
from app.bpmn.validation import validate_bpmn
from app.chat.service import ChatServiceError, handle_chat_message
from app.db import repository
from app.schemas.chat import ChatApplyRequest, ChatMessageRequest, ChatMessageResult, DiagramDiff

from .deps import DbDep, LLMDep

router = APIRouter(prefix="/api/processes/{process_id}/chat", tags=["chat"])


@router.post("/messages", response_model=ChatMessageResult)
async def send_chat_message(process_id: str, body: ChatMessageRequest, db: DbDep, llm: LLMDep) -> ChatMessageResult:
    repository.get_process(db, process_id)  # 404s if missing

    schema = repository.get_process_schema(db, process_id)
    if schema is None or not schema.elements:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="No extracted process data yet -- upload and wait for at least one document to finish "
            "processing, or generate a draft BPMN, before using chat",
        )

    history = repository.list_chat_messages(db, process_id)

    try:
        reply = await handle_chat_message(
            llm,
            schema=schema,
            history=history,
            selected_element_id=body.selected_element_id,
            text=body.text,
        )
    except ChatServiceError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return repository.add_chat_message(
        db,
        process_id,
        request_text=body.text,
        selected_element_id=body.selected_element_id,
        kind=reply.kind,
        reply_text=reply.reply_text,
        proposed_diff=reply.diff.model_dump() if reply.diff is not None else None,
        needs_confirmation=reply.needs_confirmation,
    )


@router.get("/messages", response_model=list[ChatMessageResult])
def list_chat_messages(process_id: str, db: DbDep) -> list[ChatMessageResult]:
    return repository.list_chat_messages(db, process_id)


@router.post("/messages/{message_id}/apply", response_model=ChatMessageResult)
def apply_chat_message(process_id: str, message_id: str, body: ChatApplyRequest, db: DbDep) -> ChatMessageResult:
    message = repository.get_chat_message(db, process_id, message_id)

    if not message.needs_confirmation:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This chat message has no pending diagram change")
    if message.applied or message.declined:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This chat message has already been decided")

    if not body.confirm:
        return repository.mark_chat_message_decided(db, message, applied=False)

    # bpmn-chat-ops hard rule: parse intent -> build diff (already done,
    # POST /messages) -> present -> confirm (here) -> apply diff -> validate
    # result -> log to audit trail. Nothing below is persisted unless the
    # regenerated diagram validates clean.
    schema = repository.get_process_schema(db, process_id)
    if schema is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Process has no schema to apply this change to")

    diff = message.proposed_diff
    if diff is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This chat message has no diff to apply")

    try:
        updated_schema = apply_diagram_diff(schema, DiagramDiff.model_validate(diff))
    except DiagramDiffError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Could not apply diagram diff: {exc}") from exc

    current_draft = repository.get_draft_bpmn(db, process_id)
    preferred_positions = extract_node_positions(current_draft.xml) if current_draft else {}
    # A generated draft from imperfect extracted data can already carry
    # validation issues unrelated to this edit (POST /bpmn/generate
    # deliberately doesn't block on those -- see app/api/bpmn.py). Blocking
    # here on ALL issues would make chat editing unusable on any such
    # diagram; only block if the edit leaves MORE issues than before.
    #
    # Deliberately a count comparison, not "any issue string not seen
    # before": found live that a legitimate append-to-the-end edit moves
    # the "no outgoing flow" complaint from the old last node (now fixed,
    # it has an outgoing flow to the new node) to the new last node (which
    # doesn't have one yet either) -- same total problem, different node
    # id in the message text, so a strict set-difference wrongly treated a
    # net-neutral edit as "introducing a new problem" and blocked it.
    pre_existing_issues = validate_bpmn(current_draft.xml) if current_draft else []

    xml, low_confidence_element_ids = build_bpmn_xml(process_id, updated_schema, preferred_positions)
    new_issues = validate_bpmn(xml)
    if len(new_issues) > len(pre_existing_issues):
        # humanize_validation_issues swaps raw BPMN ids (meaningless to a
        # Process Analyst) for the element/flow's own label, using the
        # post-edit schema so a newly-added node's id resolves too.
        introduced_issues = [issue for issue in new_issues if issue not in pre_existing_issues] or new_issues
        readable_issues = humanize_validation_issues(introduced_issues, updated_schema)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Applying this change would break the diagram: " + "; ".join(readable_issues),
        )

    repository.set_process_schema(db, process_id, updated_schema)
    repository.set_draft_bpmn(db, process_id, xml, low_confidence_element_ids)
    return repository.mark_chat_message_decided(db, message, applied=True)
