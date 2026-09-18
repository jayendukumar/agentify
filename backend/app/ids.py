"""Shared ID/timestamp helpers. Standalone (no dependency on app.store or
app.schemas) so both can import it without a circular import -- notably
app/ingestion/merge.py needs new_id() and is imported by app/store.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
