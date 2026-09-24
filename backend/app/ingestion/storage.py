from __future__ import annotations

from pathlib import Path

from app.config import Settings


def _document_path(settings: Settings, process_id: str, document_id: str, filename: str) -> Path:
    return settings.document_storage_root / process_id / f"{document_id}_{filename}"


def save_uploaded_file(settings: Settings, process_id: str, document_id: str, filename: str, data: bytes) -> Path:
    path = _document_path(settings, process_id, document_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def read_uploaded_file(settings: Settings, process_id: str, document_id: str, filename: str) -> bytes:
    return _document_path(settings, process_id, document_id, filename).read_bytes()


def delete_uploaded_file(settings: Settings, process_id: str, document_id: str, filename: str) -> None:
    _document_path(settings, process_id, document_id, filename).unlink(missing_ok=True)
