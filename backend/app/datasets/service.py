"""Epic 32: labelled datasets -- revisioned rows, historical import with
local pseudonymization, label review, ambiguity, version diffs, holdout
splits, and promoting a run into a golden case.

Row revisions: a change never edits a row in place. It closes the current
revision at the dataset's next version and opens a new one, so
`rows_at(version)` reproduces the dataset exactly as any run saw it.
"""

from __future__ import annotations

import csv
import hashlib
import io
import random
import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentArtifactModel,
    DatasetRowModel,
    LabelledDatasetModel,
    ProcessModel,
    SimulationRunModel,
    TwinScenarioModel,
)
from app.ids import new_id, utcnow
from app.schemas.datasets import (
    AmbiguityUpdate,
    ColumnMapping,
    DatasetCreate,
    DatasetDiff,
    DatasetRow,
    DatasetRowInput,
    DatasetRowPage,
    DatasetRowUpdate,
    DatasetSummary,
    ImportPreview,
    ImportResult,
    PromoteRunRequest,
    RowChange,
    RowLabels,
)
from app.store import NotFoundError


class DatasetError(Exception):
    pass


# ---- datasets -------------------------------------------------------------


def get_dataset_model(session: Session, process_id: str, dataset_id: str) -> LabelledDatasetModel:
    dataset = session.get(LabelledDatasetModel, dataset_id)
    if dataset is None or dataset.process_id != process_id:
        raise NotFoundError("dataset", dataset_id)
    return dataset


def create_dataset(session: Session, process_id: str, data: DatasetCreate, *, created_by: str | None) -> LabelledDatasetModel:
    if session.get(ProcessModel, process_id) is None:
        raise NotFoundError("process", process_id)
    if data.agent_artifact_id is not None:
        artifact = session.get(AgentArtifactModel, data.agent_artifact_id)
        if artifact is None or artifact.process_id != process_id:
            raise NotFoundError("agent artifact", data.agent_artifact_id)
    dataset = LabelledDatasetModel(
        id=new_id("dataset"),
        process_id=process_id,
        agent_artifact_id=data.agent_artifact_id,
        name=data.name,
        description=data.description,
        version=1,
        created_by=created_by,
    )
    session.add(dataset)
    session.flush()
    return dataset


def rows_at(session: Session, dataset_id: str, version: int | None = None) -> list[DatasetRowModel]:
    """The dataset's rows as of `version` (current if None)."""
    query = select(DatasetRowModel).where(DatasetRowModel.dataset_id == dataset_id)
    if version is None:
        query = query.where(DatasetRowModel.valid_to_version.is_(None))
    else:
        query = query.where(
            DatasetRowModel.valid_from_version <= version,
            or_(DatasetRowModel.valid_to_version.is_(None), DatasetRowModel.valid_to_version > version),
        )
    return list(session.scalars(query.order_by(DatasetRowModel.created_at, DatasetRowModel.row_key)))


def current_row(session: Session, dataset_id: str, row_key: str) -> DatasetRowModel:
    row = session.scalar(
        select(DatasetRowModel).where(
            DatasetRowModel.dataset_id == dataset_id,
            DatasetRowModel.row_key == row_key,
            DatasetRowModel.valid_to_version.is_(None),
        )
    )
    if row is None:
        raise NotFoundError("dataset row", row_key)
    return row


def row_at(session: Session, dataset_id: str, row_key: str, version: int) -> DatasetRowModel:
    for row in rows_at(session, dataset_id, version):
        if row.row_key == row_key:
            return row
    raise NotFoundError("dataset row", f"{row_key}@v{version}")


def _labels_agree(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return RowLabels.model_validate(a or {}) == RowLabels.model_validate(b or {})


def summarize(session: Session, dataset: LabelledDatasetModel) -> DatasetSummary:
    rows = rows_at(session, dataset.id)
    segments: dict[str, int] = {}
    provenance: dict[str, int] = {}
    for row in rows:
        segments[row.segment] = segments.get(row.segment, 0) + 1
        provenance[row.provenance] = provenance.get(row.provenance, 0) + 1
    double = [r for r in rows if r.second_labels]
    return DatasetSummary(
        id=dataset.id,
        process_id=dataset.process_id,
        agent_artifact_id=dataset.agent_artifact_id,
        name=dataset.name,
        description=dataset.description,
        version=dataset.version,
        row_count=len(rows),
        holdout_count=sum(1 for r in rows if r.holdout),
        ambiguous_count=sum(1 for r in rows if r.ambiguous),
        confirmed_count=sum(1 for r in rows if r.confirmed_at is not None),
        segments=segments,
        provenance=provenance,
        agreement_rate=(sum(1 for r in double if _labels_agree(r.labels, r.second_labels)) / len(double)) if double else None,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
    )


def list_datasets(session: Session, process_id: str) -> list[DatasetSummary]:
    datasets = session.scalars(
        select(LabelledDatasetModel).where(LabelledDatasetModel.process_id == process_id).order_by(LabelledDatasetModel.created_at)
    )
    return [summarize(session, d) for d in datasets]


def to_row(row: DatasetRowModel, *, include_holdout: bool = False) -> DatasetRow:
    return DatasetRow(
        row_key=row.row_key,
        revision_id=row.id,
        valid_from_version=row.valid_from_version,
        inputs=row.inputs or {},
        labels=None if row.holdout and not include_holdout else RowLabels.model_validate(row.labels or {}),
        segment=row.segment,
        provenance=row.provenance,  # type: ignore[arg-type]
        tags=row.tags or [],
        critical=row.critical,
        ambiguous=row.ambiguous,
        ambiguity_note=row.ambiguity_note,
        second_labels=RowLabels.model_validate(row.second_labels) if row.second_labels else None,
        holdout=row.holdout,
        source_run_id=row.source_run_id,
        confirmed_by=row.confirmed_by,
        confirmed_at=row.confirmed_at,
    )


def list_rows(
    session: Session,
    dataset: LabelledDatasetModel,
    *,
    version: int | None = None,
    segment: str | None = None,
    provenance: str | None = None,
    only: str | None = None,
    include_holdout: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> DatasetRowPage:
    rows = rows_at(session, dataset.id, version)
    if segment:
        rows = [r for r in rows if r.segment == segment]
    if provenance:
        rows = [r for r in rows if r.provenance == provenance]
    if only == "unconfirmed":
        rows = [r for r in rows if r.confirmed_at is None]
    elif only == "ambiguous":
        rows = [r for r in rows if r.ambiguous]
    elif only == "holdout":
        rows = [r for r in rows if r.holdout]
    elif only == "critical":
        rows = [r for r in rows if r.critical]
    return DatasetRowPage(
        version=version or dataset.version,
        total=len(rows),
        rows=[to_row(r, include_holdout=include_holdout) for r in rows[offset : offset + limit]],
    )


# ---- writing rows (each write = one new dataset version) ------------------


def _bump(dataset: LabelledDatasetModel) -> int:
    dataset.version += 1
    dataset.updated_at = utcnow().replace(tzinfo=None)
    return dataset.version


def _new_revision(
    dataset: LabelledDatasetModel, version: int, *, base: DatasetRowModel | None = None, **fields: Any
) -> DatasetRowModel:
    values = {
        "row_key": base.row_key if base else new_id("row"),
        "inputs": base.inputs if base else {},
        "labels": base.labels if base else {},
        "segment": base.segment if base else "typical",
        "provenance": base.provenance if base else "expert",
        "tags": base.tags if base else [],
        "ambiguous": base.ambiguous if base else False,
        "ambiguity_note": base.ambiguity_note if base else None,
        "second_labels": base.second_labels if base else None,
        "holdout": base.holdout if base else False,
        "critical": base.critical if base else False,
        "source_run_id": base.source_run_id if base else None,
        "confirmed_by": base.confirmed_by if base else None,
        "confirmed_at": base.confirmed_at if base else None,
        "created_by": base.created_by if base else None,
    }
    values.update(fields)
    if base is not None:
        base.valid_to_version = version
    return DatasetRowModel(id=new_id("rowrev"), dataset_id=dataset.id, valid_from_version=version, **values)


def add_rows(
    session: Session, dataset: LabelledDatasetModel, rows: list[DatasetRowInput], *, created_by: str | None
) -> int:
    if not rows:
        return dataset.version
    existing_keys = {r.row_key for r in rows_at(session, dataset.id)}
    version = _bump(dataset)
    for row in rows:
        key = row.row_key or new_id("row")
        if key in existing_keys:
            raise DatasetError(f"Row '{key}' already exists")
        existing_keys.add(key)
        session.add(
            _new_revision(
                dataset,
                version,
                row_key=key,
                inputs=row.inputs,
                labels=row.labels.model_dump(),
                segment=row.segment,
                provenance=row.provenance,
                tags=row.tags,
                critical=row.critical,
                created_by=created_by,
            )
        )
    session.flush()
    return version


def update_row(
    session: Session, dataset: LabelledDatasetModel, row_key: str, data: DatasetRowUpdate, *, changed_by: str | None
) -> DatasetRowModel:
    """US32.4: a person correcting or confirming a label. Confirming marks
    who stands behind the label."""
    base = current_row(session, dataset.id, row_key)
    fields: dict[str, Any] = {}
    if data.inputs is not None:
        fields["inputs"] = data.inputs
    if data.labels is not None:
        fields["labels"] = data.labels.model_dump()
    if data.segment is not None:
        fields["segment"] = data.segment
    if data.tags is not None:
        fields["tags"] = data.tags
    if data.critical is not None:
        fields["critical"] = data.critical
    if data.confirm or data.labels is not None:
        fields["confirmed_by"] = changed_by
        fields["confirmed_at"] = utcnow().replace(tzinfo=None)
        if data.labels is not None and base.provenance in ("synthetic", "oracle", "historical"):
            fields["provenance"] = "expert"  # a person set this label
    version = _bump(dataset)
    row = _new_revision(dataset, version, base=base, **fields)
    session.add(row)
    session.flush()
    return row


def set_ambiguity(
    session: Session, dataset: LabelledDatasetModel, row_key: str, data: AmbiguityUpdate
) -> DatasetRowModel:
    base = current_row(session, dataset.id, row_key)
    version = _bump(dataset)
    row = _new_revision(
        dataset,
        version,
        base=base,
        ambiguous=data.ambiguous,
        ambiguity_note=data.note,
        second_labels=data.second_labels.model_dump() if data.second_labels else base.second_labels,
    )
    session.add(row)
    session.flush()
    return row


def remove_row(session: Session, dataset: LabelledDatasetModel, row_key: str) -> None:
    base = current_row(session, dataset.id, row_key)
    base.valid_to_version = _bump(dataset)
    session.flush()


def split_holdout(session: Session, dataset: LabelledDatasetModel, *, fraction: float, seed: int) -> int:
    """US32.8: stratified by segment, reproducible from the seed."""
    rows = rows_at(session, dataset.id)
    by_segment: dict[str, list[DatasetRowModel]] = {}
    for row in rows:
        by_segment.setdefault(row.segment, []).append(row)
    rng = random.Random(seed)
    chosen: set[str] = set()
    for segment_rows in by_segment.values():
        keys = sorted(r.row_key for r in segment_rows)
        rng.shuffle(keys)
        chosen.update(keys[: round(len(keys) * fraction)])
    version = _bump(dataset)
    for row in rows:
        holdout = row.row_key in chosen
        if holdout != row.holdout:
            session.add(_new_revision(dataset, version, base=row, holdout=holdout))
    session.flush()
    return len(chosen)


# ---- diff (US32.6) --------------------------------------------------------


def _row_dict(row: DatasetRowModel) -> dict[str, Any]:
    return {
        "inputs": row.inputs,
        "labels": row.labels,
        "segment": row.segment,
        "ambiguous": row.ambiguous,
        "holdout": row.holdout,
        "critical": row.critical,
    }


def diff(session: Session, dataset: LabelledDatasetModel, from_version: int, to_version: int) -> DatasetDiff:
    before = {r.row_key: r for r in rows_at(session, dataset.id, from_version)}
    after = {r.row_key: r for r in rows_at(session, dataset.id, to_version)}
    result = DatasetDiff(from_version=from_version, to_version=to_version)
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old is None and new is not None:
            result.added += 1
            result.changes.append(RowChange(row_key=key, change="added", after=_row_dict(new)))
        elif new is None and old is not None:
            result.removed += 1
            result.changes.append(RowChange(row_key=key, change="removed", before=_row_dict(old)))
        elif old is not None and new is not None and old.id != new.id:
            if (old.labels or {}) != (new.labels or {}):
                result.relabelled += 1
                change = "relabelled"
            elif _row_dict(old) != _row_dict(new):
                result.edited += 1
                change = "edited"
            else:
                continue
            result.changes.append(RowChange(row_key=key, change=change, before=_row_dict(old), after=_row_dict(new)))
    return result


# ---- historical import (US32.3) -------------------------------------------

_LABEL_HINTS = re.compile(r"^(expected|outcome|final|label|result|decision|status)", re.I)
_DECISION_VALUES = {"approve": "approve", "approved": "approve", "accept": "approve", "accepted": "approve",
                    "yes": "approve", "paid": "approve", "reject": "reject", "rejected": "reject",
                    "decline": "reject", "declined": "reject", "no": "reject", "denied": "reject",
                    "escalate": "escalate", "escalated": "escalate", "referred": "escalate", "review": "escalate"}


def normalize_decision(value: Any) -> str | None:
    return _DECISION_VALUES.get(str(value).strip().lower()) if value not in (None, "") else None

_FAKE_FIRST = ["Alex", "Sam", "Jordan", "Robin", "Taylor", "Morgan", "Casey", "Jamie", "Avery", "Riley"]
_FAKE_LAST = ["Meyer", "Novak", "Silva", "Okafor", "Lindqvist", "Moreau", "Tanaka", "Kowalski", "Byrne", "Haddad"]


def _digest(value: str) -> str:
    return hashlib.sha256(f"axyntro-pseudonym:{value}".encode()).hexdigest()


def pseudonymize(value: Any, mode: str) -> Any:
    """Deterministic and local -- the same input always maps to the same
    stand-in (so joins and duplicates survive) and nothing leaves the
    machine. Shapes are kept: an email stays an email, digits stay digits."""
    if mode == "keep" or value in (None, ""):
        return value
    text = str(value)
    digest = _digest(text)
    if mode == "hash":
        return f"anon_{digest[:10]}"
    if "@" in text:
        return f"user{int(digest[:6], 16) % 100000}@example.com"
    if re.fullmatch(r"[\d\s()+.-]{6,}", text):
        digits = iter(str(int(digest, 16))[: sum(c.isdigit() for c in text)].ljust(20, "7"))
        return "".join(next(digits) if c.isdigit() else c for c in text)
    if re.fullmatch(r"[A-Za-z]{2}\d{2}[A-Za-z0-9 ]{8,}", text):
        return text[:4] + "".join(str(int(c, 16) % 10) for c in digest[: len(text) - 4])
    first = _FAKE_FIRST[int(digest[:4], 16) % len(_FAKE_FIRST)]
    if " " in text.strip():
        return f"{first} {_FAKE_LAST[int(digest[4:8], 16) % len(_FAKE_LAST)]}"
    return first


def _read_table(content: bytes, filename: str) -> tuple[list[str], list[dict[str, Any]]]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError as exc:
            raise DatasetError("XLSX import needs the openpyxl package -- export the sheet as CSV instead") from exc
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.worksheets[0]
        values = list(sheet.iter_rows(values_only=True))
        if not values:
            return [], []
        headers = [str(h).strip() if h is not None else f"column_{i + 1}" for i, h in enumerate(values[0])]
        return headers, [dict(zip(headers, row)) for row in values[1:] if any(v is not None for v in row)]
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DatasetError("The file has no header row")
    return list(reader.fieldnames), [dict(r) for r in reader]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _looks_like_decisions(values: list[Any]) -> bool:
    present = [v for v in values if v not in (None, "")]
    return bool(present) and all(normalize_decision(v) is not None for v in present)


def suggest_mapping(
    columns: list[str],
    input_fields: list[str],
    output_fields: list[str],
    records: list[dict[str, Any]] | None = None,
) -> list[ColumnMapping]:
    """A first guess a person then confirms: agent input/output field names
    win, then a column whose values are all decisions (approved/declined/
    escalated...), then label-ish names ("expected_", "final", "outcome")."""
    inputs = {_norm(f): f for f in input_fields}
    outputs = {_norm(f): f for f in output_fields}
    sample = (records or [])[:50]
    mapping = []
    for column in columns:
        key = _norm(column)
        stripped = _norm(_LABEL_HINTS.sub("", column))
        if key in inputs:
            mapping.append(ColumnMapping(column=column, role="input", field=inputs[key]))
        elif key not in outputs and sample and _looks_like_decisions([r.get(column) for r in sample]):
            mapping.append(ColumnMapping(column=column, role="decision"))
        elif key in outputs or (stripped and stripped in outputs):
            mapping.append(ColumnMapping(column=column, role="label", field=outputs.get(key) or outputs[stripped]))
        elif key in ("segment", "category", "casetype"):
            mapping.append(ColumnMapping(column=column, role="segment"))
        elif _LABEL_HINTS.match(column) and "decision" in key or key in ("approved", "approvaldecision"):
            mapping.append(ColumnMapping(column=column, role="decision"))
        elif _LABEL_HINTS.match(column):
            mapping.append(ColumnMapping(column=column, role="label", field=column))
        else:
            mapping.append(ColumnMapping(column=column, role="input", field=column))
    return mapping


def map_row(record: dict[str, Any], mapping: list[ColumnMapping], *, segment: str = "historical") -> DatasetRowInput:
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    decision = None
    tags: list[str] = []
    row_segment = segment
    for column in mapping:
        value = record.get(column.column)
        if isinstance(value, str):
            value = value.strip()
        value = pseudonymize(value, column.pseudonymize)
        if column.role == "input" and value not in (None, ""):
            inputs[column.field or column.column] = value
        elif column.role == "label" and value not in (None, ""):
            outputs[column.field or column.column] = value
        elif column.role == "decision" and value not in (None, ""):
            decision = normalize_decision(value)
        elif column.role == "segment" and value:
            row_segment = str(value)
        elif column.role == "tag" and value:
            tags.append(str(value))
    return DatasetRowInput(
        inputs=inputs,
        labels=RowLabels(outputs=outputs, decision=decision),
        segment=row_segment,
        provenance="historical",
        tags=tags,
    )


def _agent_fields(session: Session, dataset: LabelledDatasetModel) -> tuple[list[str], list[str]]:
    artifact = session.get(AgentArtifactModel, dataset.agent_artifact_id) if dataset.agent_artifact_id else None
    definition = (artifact.definition or {}) if artifact else {}
    return (
        [f["name"] for f in definition.get("input_schema", [])],
        [f["name"] for f in definition.get("output_schema", [])],
    )


def preview_import(session: Session, dataset: LabelledDatasetModel, content: bytes, filename: str) -> ImportPreview:
    columns, records = _read_table(content, filename)
    suggested = suggest_mapping(columns, *_agent_fields(session, dataset), records)
    return ImportPreview(
        columns=columns,
        suggested=suggested,
        sample=[{k: (str(v) if v is not None else None) for k, v in r.items()} for r in records[:5]],
        total_rows=len(records),
        mapped_sample=[map_row(r, suggested) for r in records[:5]],
    )


def import_rows(
    session: Session,
    dataset: LabelledDatasetModel,
    content: bytes,
    filename: str,
    mapping: list[ColumnMapping],
    *,
    created_by: str | None,
) -> ImportResult:
    """Only the mapped, pseudonymized rows are stored -- never the file."""
    _, records = _read_table(content, filename)
    rows = []
    skipped = 0
    for record in records:
        row = map_row(record, mapping)
        if not row.inputs:
            skipped += 1
            continue
        rows.append(row)
    version = add_rows(session, dataset, rows, created_by=created_by)
    return ImportResult(version=version, added=len(rows), skipped=skipped)


# ---- promote a run (US32.7) -----------------------------------------------


def promote_run(
    session: Session, dataset: LabelledDatasetModel, data: PromoteRunRequest, *, created_by: str | None
) -> DatasetRowModel:
    run = session.get(SimulationRunModel, data.run_id)
    if run is None or run.process_id != dataset.process_id:
        raise NotFoundError("run", data.run_id)
    if run.row_key and run.dataset_id:
        inputs = row_at(session, run.dataset_id, run.row_key, run.dataset_version or 1).inputs
    else:
        scenario = session.get(TwinScenarioModel, run.scenario_id)
        if scenario is None:
            raise DatasetError("The run's scenario no longer exists, so its inputs can't be recovered")
        inputs = scenario.inputs or {}
    version = _bump(dataset)
    row = _new_revision(
        dataset,
        version,
        inputs=inputs,
        labels=data.labels.model_dump(),
        segment=data.segment,
        provenance="expert",
        critical=data.critical,
        source_run_id=run.id,
        confirmed_by=created_by,
        confirmed_at=utcnow().replace(tzinfo=None),
        created_by=created_by,
    )
    session.add(row)
    session.flush()
    return row
