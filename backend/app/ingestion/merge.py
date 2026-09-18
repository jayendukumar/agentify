"""US1.8: conflict-aware merge of a newly-extracted ProcessSchema into an
existing one, per the process-doc-ingestion skill's multi-document merge
rules. Uses string similarity (difflib) as the matching signal -- there are
no embeddings yet (planning/document-ingestion-strategy.md defers that to
Epic 2), so this is a heuristic proxy for semantic matching, not the real
thing.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from app.schemas.common import Actor, ProcessElement, ProcessSchema

# Ratio above which two element labels are treated as plausibly describing
# the same step. Tuned high (rather than a looser threshold) because a
# false-positive merge silently conflates two distinct steps -- a false
# negative just leaves two similar-looking elements unmerged, which is the
# safer failure mode and still visible to a human reviewing the result.
_LABEL_MATCH_THRESHOLD = 0.85

_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _label_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def _actor_key(actor: Actor) -> tuple[str, str]:
    return (actor.name.strip().lower(), actor.type)


def _highest_confidence(a: str, b: str) -> str:
    return a if _CONFIDENCE_RANK[a] >= _CONFIDENCE_RANK[b] else b


def merge_process_schemas(existing: ProcessSchema, new: ProcessSchema) -> ProcessSchema:
    """Merge `new` into `existing` in place, and return it.

    Actors are deduped by (name, type). Elements are matched to an existing
    element by label similarity; a match with agreeing actors is treated as
    the same step and merged (unioning source_refs/inputs/outputs/systems,
    keeping the higher confidence). A label match with *disagreeing* actors
    is a genuine conflict -- both candidates are kept, per the skill, and
    both are downgraded to "low" confidence so the disagreement surfaces
    for review instead of one being silently discarded. Flows are remapped
    through whatever elements got merged away, and exact-duplicate flows
    are skipped.
    """
    actor_remap: dict[str, str] = {}
    existing_actor_keys = {_actor_key(a): a.id for a in existing.actors}
    for actor in new.actors:
        key = _actor_key(actor)
        if key in existing_actor_keys:
            actor_remap[actor.id] = existing_actor_keys[key]
        else:
            existing.actors.append(actor)
            existing_actor_keys[key] = actor.id

    def resolved_actor_id(element: ProcessElement) -> str | None:
        if element.actor_id is None:
            return None
        return actor_remap.get(element.actor_id, element.actor_id)

    element_remap: dict[str, str] = {}
    for element in new.elements:
        element_actor_id = resolved_actor_id(element)

        best_match: ProcessElement | None = None
        best_ratio = 0.0
        for candidate in existing.elements:
            ratio = _label_similarity(candidate.label, element.label)
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = candidate

        if best_match is not None and best_ratio >= _LABEL_MATCH_THRESHOLD:
            actors_agree = (
                element_actor_id is None or best_match.actor_id is None or element_actor_id == best_match.actor_id
            )
            if actors_agree:
                if element_actor_id is not None:
                    best_match.actor_id = element_actor_id
                for ref in element.source_refs:
                    if ref not in best_match.source_refs:
                        best_match.source_refs.append(ref)
                for value in element.inputs:
                    if value not in best_match.inputs:
                        best_match.inputs.append(value)
                for value in element.outputs:
                    if value not in best_match.outputs:
                        best_match.outputs.append(value)
                for value in element.systems_touched:
                    if value not in best_match.systems_touched:
                        best_match.systems_touched.append(value)
                best_match.confidence = _highest_confidence(best_match.confidence, element.confidence)
                element_remap[element.id] = best_match.id
                continue

            best_match.confidence = "low"
            existing.elements.append(element.model_copy(update={"actor_id": element_actor_id, "confidence": "low"}))
            continue

        existing.elements.append(element.model_copy(update={"actor_id": element_actor_id}))

    existing_flow_keys = {(f.from_, f.to, f.condition) for f in existing.flows}
    for flow in new.flows:
        remapped_from = element_remap.get(flow.from_, flow.from_)
        remapped_to = element_remap.get(flow.to, flow.to)
        key = (remapped_from, remapped_to, flow.condition)
        if key in existing_flow_keys:
            continue
        existing.flows.append(flow.model_copy(update={"from_": remapped_from, "to": remapped_to}))
        existing_flow_keys.add(key)

    return existing
