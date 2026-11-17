"""Strict new-output boundary; historical models remain readable separately.

Controlled values come from versioned YAML; D01–D13 semantics are checked
only on new outputs. Historical Pydantic models remain a separate read boundary.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast, get_args

from pydantic import BaseModel

from .schemas import SCHOOL_TO_CODE, AnnotatedChunkV2, CardCombinationChunkV2
from .semantic_validation import METHOD_SECTIONS, normalize_semantic_structure, validate_semantics
from .vocab_loader import VocabularyRegistry, get_registry


@dataclass(frozen=True)
class TermField:
    school: str
    paths: tuple[str, ...]
    key: str = "canonical"


def _field(school: str, *paths: str, key: str = "canonical") -> TermField:
    return TermField(school, paths, key)


CARDS = _field("塔罗", "major_arcana", "minor_arcana")
STARS = _field("紫微", "stars")
PALACES = _field("紫微", "palaces.twelve_palaces", "palaces.special_palaces")

# Only fields with an unambiguous existing vocabulary binding belong here.
# Free text (evidence, spread_position, keywords) is deliberately not an enum.
TERM_FIELDS: dict[str, dict[str, TermField]] = {
    "SihuaMention": {
        "star": STARS,
        "palace": PALACES,
        "source_palace": PALACES,
        "target_palace": PALACES,
        "transformation": _field("紫微", "transformations.types"),
    },
    "TimeContextMention": {
        "time_context": _field("紫微", "time_contexts"),
        "chart_type": _field("紫微", "chart_types"),
    },
    "PatternMention": {
        "pattern_name": _field("紫微", "patterns"),
        "formation_status": _field("紫微", "formation_status"),
    },
    "ZiweiExtension": {
        "knowledge_domain": _field("紫微", "knowledge_domains"),
        "stars_mentioned": STARS,
        "palaces_mentioned": PALACES,
        "star_pairs_mentioned": _field("紫微", "star_pairs"),
        "star_groups_mentioned": _field("紫微", "star_groups"),
        "school_variant": _field("紫微", "school_variants"),
        "topics_mentioned": _field("紫微", "topics"),
        "time_context": _field("紫微", "time_contexts"),
        "star_interactions_mentioned": _field("紫微", "star_interactions", key="name"),
    },
    "HexagramMention": {"hexagram_name": _field("六爻", "hexagrams")},
    "YaoMention": {"six_relative": _field("六爻", "six_relatives"), "six_spirit": _field("六爻", "six_spirits")},
    "LiuyaoExtension": {
        "knowledge_domain": _field("六爻", "knowledge_domains"),
        "six_relatives_mentioned": _field("六爻", "six_relatives"),
        "six_spirits_mentioned": _field("六爻", "six_spirits"),
        "divination_method": _field("六爻", "divination_methods"),
        "question_type": _field("六爻", "topics"),
        "trigrams_mentioned": _field("六爻", "trigrams"),
        "five_phases_mentioned": _field("六爻", "five_phases"),
        "line_statuses_mentioned": _field("六爻", "line_statuses"),
        "hexagram_roles_mentioned": _field("六爻", "hexagram_roles"),
    },
    "CardMention": {"card_name": CARDS, "position": _field("塔罗", "positions")},
    "TarotExtension": {
        "knowledge_domain": _field("塔罗", "knowledge_domains"),
        "spread_used": _field("塔罗", "spreads"),
        "elements_mentioned": _field("塔罗", "elements"),
        "suits_mentioned": _field("塔罗", "suits"),
        "reading_topic": _field("塔罗", "topics"),
        "card_combinations_mentioned": CARDS,
        "reading_technique": _field("塔罗", "reading_techniques"),
        "spreads_mentioned": _field("塔罗", "spreads"),
        "positions_mentioned": _field("塔罗", "positions"),
        "reading_techniques_mentioned": _field("塔罗", "reading_techniques"),
    },
    "PlanetMention": {"planet": _field("西占", "planets"), "sign": _field("西占", "zodiac_signs")},
    "AspectMention": {
        "planet1": _field("西占", "planets"),
        "planet2": _field("西占", "planets"),
        "aspect_type": _field("西占", "aspects"),
    },
    "WesternExtension": {
        "knowledge_domain": _field("西占", "knowledge_domains"),
        "signs_mentioned": _field("西占", "zodiac_signs"),
        "retrograde_planets": _field("西占", "planets"),
        "house_system": _field("西占", "house_systems"),
        "chart_type": _field("西占", "chart_types"),
        "elements_mentioned": _field("西占", "elements"),
        "modalities_mentioned": _field("西占", "modalities"),
        "dignities_mentioned": _field("西占", "dignities"),
        "fixed_stars_mentioned": _field("西占", "fixed_stars"),
        "lunar_phase": _field("西占", "lunar_phases"),
        "analysis_type_mentioned": _field("西占", "analysis_type"),
    },
    "CardCombinationChunkV2": {"card_combinations_mentioned": CARDS},
}


for _model, _school in [
    ("ZiweiExtension", "紫微"),
    ("LiuyaoExtension", "六爻"),
    ("TarotExtension", "塔罗"),
    ("WesternExtension", "西占"),
]:
    TERM_FIELDS[_model].update(
        {
            "knowledge_domains": _field(_school, "knowledge_domains"),
            "topics_mentioned": _field(_school, "topics"),
            "analysis_methods": _field(_school, METHOD_SECTIONS[_school]),
        }
    )
for _model, _school in [("ZiweiExtension", "紫微"), ("WesternExtension", "西占")]:
    TERM_FIELDS[_model].update(
        {"chart_type": _field(_school, "chart_types"), "chart_types_mentioned": _field(_school, "chart_types")}
    )
TERM_FIELDS["ZiweiExtension"].update(
    {
        "relationship_types_mentioned": _field("紫微", "relationship_types"),
        "comparison_methods": _field("紫微", "comparison_methods"),
    }
)
TERM_FIELDS["LiuyaoExtension"]["yong_shen"] = _field("六爻", "six_relatives")
TERM_FIELDS["HexagramMention"].update(
    {"palace": _field("六爻", "hexagram_palaces"), "derived_palace": _field("六爻", "hexagram_palaces")}
)
TERM_FIELDS["YaoMention"]["polarity"] = _field("六爻", "line_polarities")
TERM_FIELDS["SpiritRoleMention"] = {
    "role": _field("六爻", "yong_shen_rules.spirit_roles"),
    "six_relative": _field("六爻", "six_relatives"),
}
TERM_FIELDS["TarotExtension"]["spreads_used"] = _field("塔罗", "spreads")
TERM_FIELDS["SpreadMention"] = {"spread": _field("塔罗", "spreads")}
TERM_FIELDS["LayoutMention"] = {"layout_mode": _field("塔罗", "layout_modes")}


def _entries(node: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        if isinstance(node.get(key), str):
            return [node]
        return [entry for child in node.values() for entry in _entries(child, key)]
    if isinstance(node, list):
        return [entry for child in node for entry in _entries(child, key)]
    return []


def term_options(spec: TermField, registry: VocabularyRegistry) -> tuple[set[str], dict[str, str]]:
    """One canonical set and ambiguity-safe aliases across all bound sections."""
    rows = [e for p in spec.paths for e in _entries(registry.get_field_metadata(spec.school, p), spec.key)]
    canonical = {e[spec.key] for e in rows}
    if not canonical:
        raise ValueError(f"Empty annotation vocabulary: {spec.school}/{spec.paths}")
    targets: dict[str, set[str]] = {}
    for entry in rows:
        names = [entry.get("traditional"), entry.get("english")]
        for key in ("aliases", "en_aliases", "abbreviations"):
            names.extend(entry.get(key) or [])
        for alias in names:
            if isinstance(alias, str) and alias and alias not in canonical:
                targets.setdefault(alias, set()).add(entry[spec.key])
    return canonical, {alias: next(iter(values)) for alias, values in targets.items() if len(values) == 1}


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return next((m for arg in get_args(annotation) if (m := _nested_model(arg)) is not None), None)


def _map_values(value: Any, aliases: dict[str, str]) -> Any:
    if isinstance(value, str):
        return aliases.get(value, value)
    if isinstance(value, list):
        return [_map_values(item, aliases) for item in value]
    return value


def normalize_annotation_terms(data: dict[str, Any], registry: VocabularyRegistry | None = None) -> dict[str, Any]:
    """Normalize known aliases without mutating input, dropping unknowns or inferring facts."""
    registry = registry or get_registry()

    def walk(value: Any, model: type[BaseModel]) -> Any:
        if isinstance(value, list):
            return [walk(v, model) for v in value]
        if not isinstance(value, dict):
            return value
        result = deepcopy(value)
        for name, field in model.model_fields.items():
            if name not in result:
                continue
            spec = TERM_FIELDS.get(model.__name__, {}).get(name)
            if spec:
                _, aliases = term_options(spec, registry)
                result[name] = _map_values(result[name], aliases)
            nested = _nested_model(field.annotation)
            if nested:
                result[name] = walk(result[name], nested)
        if model.__name__ == "WesternExtension" and isinstance(result.get("houses_mentioned"), list):
            spec = _field("西占", "houses")
            _, aliases = term_options(spec, registry)
            numbers = {
                e["canonical"]: e["number"]
                for e in _entries(registry.get_field_metadata("西占", "houses"), "canonical")
            }
            # Exact shorthand only; never extract a digit from arbitrary text.
            numbers.update({f"{number}宫": number for number in tuple(numbers.values())})
            result["houses_mentioned"] = [
                numbers.get(aliases.get(value, value), value) if isinstance(value, str) else value
                for value in result["houses_mentioned"]
            ]
        return result

    return normalize_semantic_structure(cast(dict[str, Any], walk(data, AnnotatedChunkV2)), registry)


def _reject_unknown_fields(data: Any, model: type[BaseModel], path: str = "") -> None:
    """Check raw keys before legacy model migrations can consume unknown fields."""
    if isinstance(data, list):
        for i, value in enumerate(data):
            _reject_unknown_fields(value, model, f"{path}[{i}]")
    elif isinstance(data, dict):
        unknown = data.keys() - model.model_fields.keys()
        if unknown:
            raise ValueError(f"{path or model.__name__}: 未知字段 {sorted(unknown)}")
        for name, field in model.model_fields.items():
            nested = _nested_model(field.annotation)
            if nested and name in data:
                _reject_unknown_fields(data[name], nested, f"{path}.{name}" if path else name)


def _validate_terms(model: BaseModel, registry: VocabularyRegistry, path: str = "") -> None:
    def check(value: Any, valid: set[str], location: str) -> None:
        if isinstance(value, list):
            for i, item in enumerate(value):
                check(item, valid, f"{location}[{i}]")
        elif value is not None and value not in valid:
            raise ValueError(f"{location}: {value!r} 不在词表 canonical 集合中")

    for name in type(model).model_fields:
        value = getattr(model, name)
        location = f"{path}.{name}" if path else name
        spec = TERM_FIELDS.get(type(model).__name__, {}).get(name)
        if spec:
            valid, _ = term_options(spec, registry)
            check(value, valid, location)
        for child in value if isinstance(value, list) else [value]:
            if isinstance(child, BaseModel):
                _validate_terms(child, registry, location)


def validate_annotation_output(
    data: Any, registry: VocabularyRegistry | None = None, *, require_evidence: bool = True
) -> AnnotatedChunkV2:
    """Validate a new ordinary annotation; does not read/write corpus or databases."""
    model = AnnotatedChunkV2.model_validate(
        data, strict=True, extra="forbid", context={"reject_cjk_en": True, "new_semantics": True}
    )
    _reject_unknown_fields(data, AnnotatedChunkV2)
    if not isinstance(data, dict) or "school_code" not in data:
        raise ValueError("新标注必须显式提供 school_code")
    code = SCHOOL_TO_CODE.get(model.school)
    if code is None or code != model.school_code:
        raise ValueError("school / school_code 不匹配或流派未知")
    for ext in ("ziwei", "liuyao", "tarot", "western"):
        if (getattr(model, ext) is not None) != (ext == code):
            raise ValueError(f"school={model.school}: {ext} 扩展与流派不匹配")
    registry = registry or get_registry()
    _validate_terms(model, registry)
    validate_semantics(model, registry, require_evidence=require_evidence)
    return model


def validate_combination_output(data: Any, registry: VocabularyRegistry | None = None) -> CardCombinationChunkV2:
    """The generated combination model remains distinct from ordinary annotations."""
    _reject_unknown_fields(data, CardCombinationChunkV2)
    model = CardCombinationChunkV2.model_validate(data, strict=True, extra="forbid")
    if model.school != "塔罗" or model.school_code != SCHOOL_TO_CODE["塔罗"] or model.content_language != "mixed":
        raise ValueError("组合记录的 school / school_code / content_language 不匹配")
    _validate_terms(model, registry or get_registry())
    return model


def annotation_output_json_schema(*, combination: bool = False) -> dict[str, Any]:
    """Export the new-output contract with structure, canonical enums and school binding."""
    model = CardCombinationChunkV2 if combination else AnnotatedChunkV2
    schema = model.model_json_schema()
    registry = get_registry()

    def enum_leaf(node: dict[str, Any], values: list[str]) -> None:
        if "items" in node:
            enum_leaf(node["items"], values)
        elif "anyOf" in node:
            for choice in node["anyOf"]:
                if choice.get("type") != "null":
                    enum_leaf(choice, values)
        else:
            node["enum"] = values

    for name, node in [(model.__name__, schema), *schema.get("$defs", {}).items()]:
        node["additionalProperties"] = False
        for field, spec in TERM_FIELDS.get(name, {}).items():
            values, _ = term_options(spec, registry)
            enum_leaf(node["properties"][field], sorted(values))
    if combination:
        for key, value in {"school": "塔罗", "school_code": "tarot", "content_language": "mixed"}.items():
            schema["properties"][key]["const"] = value
    else:
        schema["properties"]["school"]["enum"] = list(SCHOOL_TO_CODE)
        schema["required"] = sorted(set(schema.get("required", [])) | {"annotation_evidence", "summary_en"})
        schema["properties"]["summary_en"]["minLength"] = 1
        schema["properties"]["summary_en"]["pattern"] = r"^\s*\S+(?:\s+\S+){0,149}\s*$"
        schema["allOf"] = []
        for school, code in SCHOOL_TO_CODE.items():
            properties: dict[str, Any] = {"school_code": {"const": code}}
            for ext in ("ziwei", "liuyao", "tarot", "western"):
                properties[ext] = {"type": "object" if ext == code else "null"}
            if code != "generic":
                vocab = registry.load(school)
                participants: dict[str, Any] = {
                    "properties": {"entity_type": {"enum": list(vocab["relation_entities"])}},
                    "allOf": [],
                }
                for entity_type, sections in vocab["relation_entities"].items():
                    names = sorted(
                        {name for section in sections for name in registry.get_valid_values(school, section)}
                    )
                    participants["allOf"].append(
                        {
                            "if": {"properties": {"entity_type": {"const": entity_type}}},
                            "then": {"properties": {"name": {"enum": names}}},
                        }
                    )
                properties[code]["properties"] = {
                    "relations": {
                        "items": {
                            "properties": {
                                "relation_type": {"enum": [r["canonical"] for r in vocab["relation_types"]]},
                                "participants": {"items": participants},
                            }
                        }
                    }
                }
            schema["allOf"].append(
                {
                    "if": {"properties": {"school": {"const": school}}},
                    "then": {
                        "properties": properties,
                        "required": ["school_code", *([code] if code != "generic" else [])],
                    },
                }
            )
    return schema
