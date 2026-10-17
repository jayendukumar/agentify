"""Epic 38, US38.13/US38.14: walking every table a tenant owns.

The export, the erasure and the move to or from a dedicated database all
go through the same list: every table in the SQLAlchemy metadata except
the platform and operator tables, parents before children. The schema
test (tests/test_tenancy.py) proves each of them is under row-level
security, so a statement over one of them, run as the tenant, reaches
that tenant's rows and nobody else's.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Table

from app.db.models import NON_TENANT_TABLES, Base

# Sign-ins in flight and one-time links: short-lived, worthless once
# expired, and only a way in while they last. Not exported (still erased).
EXPORT_SKIPPED_TABLES = frozenset({"session_tokens", "oidc_login_requests", "support_entries"})
# Credentials, or hashes of credentials, in rows that are otherwise worth
# keeping (sign-in history, SCIM token names, who was invited).
EXPORT_REDACTED_COLUMNS = frozenset({
    ("sessions", "csrf_token"),
    ("sessions", "idp_id_token"),
    ("sessions", "idp_refresh_token"),
    ("scim_tokens", "token_hash"),
    ("tenant_invitations", "token_hash"),
})


def tenant_tables() -> list[Table]:
    """Parents before children, so inserting in this order and deleting in
    the reverse never trips a foreign key."""
    return [t for t in Base.metadata.sorted_tables if t.name not in NON_TENANT_TABLES]


def exported_columns(table: Table) -> list:
    return [c for c in table.columns if (table.name, c.name) not in EXPORT_REDACTED_COLUMNS]


def to_json(value: Any) -> Any:
    """A column value as JSON: timestamps as ISO 8601, bytes as base64,
    embeddings (pgvector, numpy) as lists of numbers."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)


def dumps(row: dict) -> bytes:
    return json.dumps(row, default=to_json, ensure_ascii=False).encode()
