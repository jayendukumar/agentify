from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status

from app.config import get_settings
from app.db import repository
from app.db.models import DocumentModel
from app.ids import new_id
from app.ingestion import pipeline, storage
from app.schemas.documents import DocumentDetail, DocumentSummary

from .deps import CurrentUserDep, DbDep, EditorDep, LLMDep

router = APIRouter(prefix="/api/processes/{process_id}/documents", tags=["documents"])

_MAX_SIZE_BYTES = 25 * 1024 * 1024


def _to_summary(document: DocumentModel) -> DocumentSummary:
    return DocumentSummary(
        id=document.id,
        process_id=document.process_id,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        status=document.status,
        process_definition_confidence=document.process_definition_confidence,
        validation_message=document.validation_message,
        created_at=document.created_at,
    )


def _to_detail(document: DocumentModel) -> DocumentDetail:
    return DocumentDetail(**_to_summary(document).model_dump(), error_message=document.error_message)


@router.post("", response_model=list[DocumentSummary], status_code=status.HTTP_201_CREATED)
async def upload_documents(
    process_id: str,
    db: DbDep,
    llm: LLMDep,
    user: EditorDep,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> list[DocumentSummary]:
    del user
    repository.get_process(db, process_id)  # 404s if the process doesn't exist
    settings = get_settings()

    results: list[DocumentSummary] = []
    for upload in files:
        content_type = upload.content_type or "application/octet-stream"
        contents = await upload.read()

        if content_type not in pipeline.SUPPORTED_CONTENT_TYPES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported content type '{content_type}' for file '{upload.filename}'",
            )
        if len(contents) > _MAX_SIZE_BYTES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"File '{upload.filename}' exceeds the {_MAX_SIZE_BYTES // (1024 * 1024)}MB limit",
            )

        filename = upload.filename or "unnamed"
        document_id = new_id("doc")
        storage.save_uploaded_file(settings, process_id, document_id, filename, contents)
        document = repository.add_document(db, process_id, document_id, filename, content_type, len(contents))
        results.append(_to_summary(document))

        # BackgroundTasks run before this request's `get_db` dependency
        # commits (that happens at generator teardown, which is after the
        # response -- including background tasks -- per FastAPI/Starlette's
        # ordering). The background task opens its own DB session/
        # connection, so without an explicit commit here it can't see this
        # document row yet: a real "document not found" 404 from the
        # background task, not a hypothetical race.
        db.commit()

        # Every content type accepted above (the check earlier in this loop)
        # is, by construction, in pipeline.SUPPORTED_CONTENT_TYPES -- both
        # checks read from the same set, so this always enqueues.
        background_tasks.add_task(pipeline.process_document, process_id, document_id, llm, settings)

    return results


@router.get("", response_model=list[DocumentSummary])
def list_documents(process_id: str, db: DbDep, user: CurrentUserDep) -> list[DocumentSummary]:
    del user
    return [_to_summary(d) for d in repository.list_documents(db, process_id)]


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(process_id: str, document_id: str, db: DbDep, user: CurrentUserDep) -> DocumentDetail:
    del user
    return _to_detail(repository.get_document(db, process_id, document_id))
