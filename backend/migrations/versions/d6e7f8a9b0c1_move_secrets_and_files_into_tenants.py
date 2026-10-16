"""move secret references and uploaded files into each tenant

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0

Epic 38, US38.10. Data only, no schema change.

- **Secret references.** A tenant's IdP client secret or connector token
  that names its own `TENANT_<SLUG>_<NAME>` environment variable becomes
  `secret://tenant/<name>`, which the default (`env`) secrets backend
  resolves to that same variable, so nothing needs setting again. Other
  environment names are left as they are: the first tenant may keep them;
  any other tenant's stop resolving and say so.
- **Files.** `<root>/<process_id>/<doc>_<name>` and the shared
  `<root>/_organization/<doc>_<name>` move to `<root>/tenants/<tenant_id>/...`.
  A file that isn't there (another machine's storage, already moved) is
  skipped; the document keeps its row either way.

Rows are read as each tenant in turn (`app.tenant_id` set per tenant),
because row-level security applies to the owner too unless it's a
superuser.
"""

import json
import re
import shutil
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def _prefix(slug: str) -> str:
    return "TENANT_" + re.sub(r"[^A-Z0-9]", "_", slug.upper()) + "_"


def _as_ref(env_name: str, slug: str) -> str | None:
    rest = env_name.removeprefix(_prefix(slug))
    if rest == env_name or not re.fullmatch(r"[A-Z0-9][A-Z0-9_]*", rest):
        return None
    return "secret://tenant/" + rest.lower()


def _safe_name(filename: str) -> str:
    return re.split(r"[\\/]", filename)[-1].strip().lstrip(".") or "unnamed"


def _move(source: Path, target: Path) -> None:
    if source.is_file() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))


def _storage_root() -> Path:
    from app.config import get_settings

    return get_settings().document_storage_root


def upgrade() -> None:
    bind = op.get_bind()
    root = _storage_root()
    tenants = bind.execute(sa.text("SELECT id, slug FROM tenants")).all()
    for tenant_id, slug in tenants:
        bind.execute(sa.text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})

        for row_id, ref in bind.execute(sa.text("SELECT id, client_secret_ref FROM tenant_identity")).all():
            new = _as_ref(ref.removeprefix("env:"), slug) if ref.startswith("env:") else None
            if new:
                bind.execute(sa.text("UPDATE tenant_identity SET client_secret_ref = :r WHERE id = :id"),
                             {"r": new, "id": row_id})

        for row_id, config in bind.execute(sa.text("SELECT id, config FROM connectors")).all():
            config = dict(config or {})
            new = _as_ref(config.get("token_env") or "", slug)
            if new and not config.get("token_ref"):
                config.pop("token_env")
                config["token_ref"] = new
                bind.execute(sa.text("UPDATE connectors SET config = CAST(:c AS json) WHERE id = :id"),
                             {"c": json.dumps(config), "id": row_id})

        mine = root / "tenants" / tenant_id
        for doc_id, process_id, filename in bind.execute(
            sa.text("SELECT id, process_id, filename FROM documents")
        ).all():
            _move(root / process_id / f"{doc_id}_{filename}", mine / process_id / f"{doc_id}_{_safe_name(filename)}")
        for doc_id, filename in bind.execute(sa.text("SELECT id, filename FROM context_documents")).all():
            _move(root / "_organization" / f"{doc_id}_{filename}",
                  mine / "_organization" / f"{doc_id}_{_safe_name(filename)}")


def downgrade() -> None:
    # References keep working with the env backend either way; files moved
    # back would need each tenant's rows again, so this is one-way.
    pass
