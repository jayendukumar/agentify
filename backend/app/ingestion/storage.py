from __future__ import annotations

import shutil
from pathlib import Path

from app.config import Settings
from app.tenancy.files import safe_name, tenant_dir, tenant_file


def _document_path(settings: Settings, process_id: str, document_id: str, filename: str) -> Path:
    """Epic 38, US38.10: under the current tenant's own directory."""
    return tenant_file(settings, process_id, f"{document_id}_{safe_name(filename)}")


def save_uploaded_file(settings: Settings, process_id: str, document_id: str, filename: str, data: bytes) -> Path:
    path = _document_path(settings, process_id, document_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def read_uploaded_file(settings: Settings, process_id: str, document_id: str, filename: str) -> bytes:
    return _document_path(settings, process_id, document_id, filename).read_bytes()


def delete_uploaded_file(settings: Settings, process_id: str, document_id: str, filename: str) -> None:
    _document_path(settings, process_id, document_id, filename).unlink(missing_ok=True)


def delete_process_files(settings: Settings, process_id: str) -> None:
    """Epic 38, US38.10: a deleted process's uploads go with it (before,
    its directory stayed behind)."""
    base = tenant_dir(settings).resolve()
    directory = (base / process_id).resolve()
    if directory.parent == base:
        shutil.rmtree(directory, ignore_errors=True)
