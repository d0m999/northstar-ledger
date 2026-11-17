"""Pure payload projections; no database access or historical data rewriting."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from .output_validation import TERM_FIELDS, _entries
from .schemas import AnnotatedChunkV2
from .vocab_loader import VocabularyRegistry


def project_semantic_metadata(chunk: AnnotatedChunkV2, registry: VocabularyRegistry) -> dict[str, Any]:
    code: str = chunk.school_code
    extension = getattr(chunk, code, None)
    if extension is None:
        # Historical constructors may not carry the separately introduced code.
        from .schemas import SCHOOL_TO_CODE

        code = SCHOOL_TO_CODE.get(chunk.school, "generic")
        extension = getattr(chunk, code, None)
    if extension is None:
        return {}
    terms: set[str] = set()
    derived_terms: set[str] = set()

    def walk(model: BaseModel, path: str = code) -> dict[str, Any]:
        result = model.model_dump()  # Explicit null retains D01's unknown state.
        for field in type(model).model_fields:
            value = getattr(model, field)
            field_path = f"{path}.{field}"
            spec = TERM_FIELDS.get(type(model).__name__, {}).get(field)
            if spec:
                entries = [
                    row
                    for section in spec.paths
                    for row in _entries(registry.get_field_metadata(spec.school, section), spec.key)
                ]
                ids = {row[spec.key]: row["id"] for row in entries if isinstance(row.get("id"), str)}

                derived = field.startswith("derived_") or any(
                    e.field_path == field_path and e.basis == "derived" for e in chunk.annotation_evidence
                )

                def convert(item: Any, ids: dict[str, str] = ids, field: str = field, derived: bool = derived) -> Any:
                    if isinstance(item, list):
                        return [convert(v) for v in item]
                    if isinstance(item, str) and item in ids:
                        if field not in {"knowledge_domain", "knowledge_domains"}:
                            (derived_terms if derived else terms).add(ids[item])
                        return ids[item]
                    return item

                result[field] = convert(value)
            elif isinstance(value, BaseModel):
                result[field] = walk(value, field_path)
            elif isinstance(value, list):
                result[field] = [
                    walk(v, f"{field_path}.{i}") if isinstance(v, BaseModel) else v for i, v in enumerate(value)
                ]
        return result

    projected = walk(extension)
    relation_ids = registry.get_term_id_map(chunk.school, "relation_types")
    scopes = {e.scope.scope_id: e.scope for e in chunk.annotation_evidence}
    relations = []
    for relation in projected["relations"]:
        relation = dict(relation)
        scope = scopes.get(relation["scope_id"])
        relation["scope_kind"] = scope.kind if scope else None
        relation["relation_type"] = relation_ids.get(relation["relation_type"], relation["relation_type"])
        for participant in relation["participants"]:
            sections = registry.load(chunk.school)["relation_entities"].get(participant["entity_type"], [])
            participant["term_ids"] = sorted(
                {
                    term_id
                    for section in sections
                    for name, term_id in registry.get_term_id_map(chunk.school, section).items()
                    if name == participant["name"]
                }
            )
            terms.update(participant["term_ids"])
        terms.add(relation["relation_type"])
        relations.append(relation)
    projected["relations"] = relations
    from .semantic_validation import _leaves

    facts = []
    objects: dict[tuple[str, str], dict[str, Any]] = {}
    for path, value in _leaves(projected, code):
        if isinstance(value, (str, int, bool)):
            exact = [e for e in chunk.annotation_evidence if e.field_path == path]
            covering = exact or [e for e in chunk.annotation_evidence if path.startswith(e.field_path + ".")]
            if not covering and ".yao_mentioned." in path and path.endswith((".ordinal", ".polarity")):
                # The registered surface encodes these coordinates directly
                # (九二 -> ordinal 2 / 阳); reuse that same object's source span.
                surface_path = path.rsplit(".", 1)[0] + ".position"
                covering = [e for e in chunk.annotation_evidence if e.field_path == surface_path]
            for evidence in covering:
                fact = {
                    "field": re.sub(r"\.\d+(?=\.|$)", "", path),
                    "value": value,
                    "scope_id": evidence.scope.scope_id,
                    "scope_kind": evidence.scope.kind,
                    "condition": evidence.scope.condition,
                    "attribution": evidence.scope.attribution,
                    "basis": evidence.basis,
                    "usage": evidence.usage,
                }
                facts.append(fact)
                object_match = re.match(r"^(.+\.\d+)\.[a-z_]+$", path)
                if object_match:
                    object_path = object_match.group(1)
                    key = (object_path, evidence.scope.scope_id)
                    group = objects.setdefault(
                        key,
                        {
                            "object_path": object_path,
                            "collection": re.sub(r"\.\d+(?=\.|$)", "", object_path),
                            "scope_id": evidence.scope.scope_id,
                            "semantic_facts": [],
                        },
                    )
                    group["semantic_facts"].append(fact)
    result: dict[str, Any] = {
        code: projected,
        "semantic_term_ids": sorted(terms),
        "semantic_derived_term_ids": sorted(derived_terms),
        "semantic_relations": relations,
        "semantic_facts": facts,
        "semantic_objects": list(objects.values()),
        "annotation_evidence": [e.model_dump() for e in chunk.annotation_evidence],
    }
    for field in (
        "chart_types_mentioned",
        "relationship_types_mentioned",
        "comparison_methods",
        "knowledge_domains",
        "topics_mentioned",
        "analysis_methods",
        "spreads_used",
        "spirit_roles",
    ):
        if field in projected:
            result[f"{code}_{field}"] = projected[field]
    return result
