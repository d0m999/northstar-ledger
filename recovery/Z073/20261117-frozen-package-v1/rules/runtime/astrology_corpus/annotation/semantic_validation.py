"""Cross-field and source-evidence checks for the D01–D13 write contract."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from .schemas import AnnotatedChunkV2
from .vocab_loader import VocabularyRegistry

STATE_FIELDS = frozenset({"is_main_hexagram", "is_changed_hexagram", "is_moving", "is_retrograde"})
METHOD_SECTIONS = {
    "紫微": "analysis_methods",
    "六爻": "analysis_methods",
    "塔罗": "reading_techniques",
    "西占": "analysis_type",
}


def value_at(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ValueError(f"unknown evidence field_path: {path}")
    return current


def normalize_semantic_structure(data: dict[str, Any], registry: VocabularyRegistry) -> dict[str, Any]:
    """Only derive line coordinates encoded in the surface; never infer case facts."""
    ext = data.get("liuyao")
    if not isinstance(ext, dict):
        return data
    forms = registry.load("六爻")["line_position_forms"]
    lookup = {row["surface"]: row for row in forms}
    for line in ext.get("yao_mentioned", []):
        if isinstance(line, dict) and line.get("position") in lookup:
            form = lookup[line["position"]]
            for field in ("ordinal", "polarity"):
                if field not in line or line[field] is None:
                    line[field] = form[field]
    return data


def _leaves(value: Any, path: str) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "scope_id":
                continue
            yield from _leaves(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _leaves(child, f"{path}.{index}")
    elif value is not None or path.rsplit(".", 1)[-1] in STATE_FIELDS:
        yield path, value


def _consistent(primary: Any, values: list[str], path: str) -> None:
    if primary is not None and primary not in values:
        raise ValueError(f"{path}: compatibility primary must occur in the multi-value field")


def validate_semantics(model: AnnotatedChunkV2, registry: VocabularyRegistry, *, require_evidence: bool = True) -> None:
    """Validate source spans, scope binding and all decided semantic invariants."""
    if len(model.summary_en.split()) > 150:
        raise ValueError("summary_en exceeds 150 words; rewrite, never truncate")
    if not model.summary_en.strip():
        raise ValueError("summary_en requires a nonempty English summary")
    quality = model.retrieval_eligibility
    if quality is not None:
        last_end = 0
        for span in quality.usable_spans:
            if (
                span.start < last_end
                or span.end > len(model.chunk_text)
                or model.chunk_text[span.start : span.end] != span.quote
            ):
                raise ValueError("usable_spans must be ordered, nonoverlapping exact source spans")
            last_end = span.end
        if quality.status == "limited":
            for item in model.annotation_evidence:
                if item.scope.condition and not any(
                    item.scope.condition in part.quote for part in quality.usable_spans
                ):
                    raise ValueError("limited usable_spans must retain the complete source condition")
                for span in item.spans:
                    if not any(part.start <= span.start < span.end <= part.end for part in quality.usable_spans):
                        raise ValueError("limited annotation evidence must stay within usable_spans")
    code = model.school_code
    ext_model = getattr(model, code, None)
    if ext_model is None:
        return
    ext = ext_model.model_dump()
    if code == "western" and "is_transit_analysis" not in ext_model.model_fields_set:
        ext.pop("is_transit_analysis", None)
    vocab = registry.load(model.school)
    if code == "ziwei":
        times = ext["time_mentions"]
        if times and ext["time_context"] is not None:
            if len(times) != 1 or times[0]["time_context"] != ext["time_context"]:
                raise ValueError("time_context cannot collapse multiple scoped time_mentions")
        for mention in times:
            expected = vocab["chart_time_context_map"].get(mention["chart_type"])
            if expected is not None and expected != mention["time_context"]:
                raise ValueError("time_context contradicts the chart in its own time mention")
    _consistent(ext.get("knowledge_domain"), ext["knowledge_domains"], f"{code}.knowledge_domain")
    _consistent(model.topic, ext["topics_mentioned"], "topic")
    if code in {"western", "ziwei"}:
        _consistent(ext["chart_type"], ext["chart_types_mentioned"], f"{code}.chart_type")
    if code == "liuyao":
        roles = ext["spirit_roles"]
        principals = {r["six_relative"] for r in roles if r["role"] == "用神" and r["six_relative"] is not None}
        if ext["yong_shen"] is not None and ext["yong_shen"] not in principals:
            raise ValueError("liuyao.yong_shen must project an evidenced Principal Spirit six-relative")
        _consistent(ext["question_type"], ext["topics_mentioned"], "liuyao.question_type")
        lookup = {row["surface"]: row for row in vocab["line_position_forms"]}
        for line in ext["yao_mentioned"]:
            if line["position"] is not None:
                if line["position"] not in lookup:
                    raise ValueError("unknown yao position surface; retain it as a source issue, not a guessed ordinal")
                form = lookup[line["position"]]
                if line["ordinal"] != form["ordinal"] or line["polarity"] != form["polarity"]:
                    raise ValueError("yao ordinal/polarity contradict its source surface")
            elif line["ordinal"] is None:
                raise ValueError("yao mention requires an ordinal or recognized position surface")
        palace_lookup = {row["canonical"]: palace for palace, rows in vocab["hexagrams"].items() for row in rows}
        for mention in ext["hexagrams_mentioned"]:
            derived = mention["derived_palace"]
            if derived is not None and palace_lookup.get(mention["hexagram_name"]) != derived:
                raise ValueError("derived palace does not match the versioned hexagram lookup")
    if code == "western":
        if ext["analysis_type_mentioned"] and set(ext["analysis_type_mentioned"]) != set(ext["analysis_methods"]):
            raise ValueError("analysis_type_mentioned must project analysis_methods")
        retrograde = {p["planet"] for p in ext["planets_mentioned"] if p["is_retrograde"] is True}
        if not set(ext["retrograde_planets"]) <= retrograde:
            raise ValueError("retrograde_planets must bind an explicitly retrograde planet mention")
    if code == "tarot":
        if ext["reading_techniques_mentioned"] and set(ext["reading_techniques_mentioned"]) != set(
            ext["analysis_methods"]
        ):
            raise ValueError("reading_techniques_mentioned must project analysis_methods")
        if any(card["card_name"] == "塔" for card in ext["cards_mentioned"]):
            stripped = re.sub(r"塔羅|塔罗|聖杯塔|金杯塔|權杖塔|錢幣塔|钱币塔", "", model.chunk_text)
            if not re.search(r"(?<![聖金權錢])塔(?![羅罗])|高塔|Tower", stripped, re.IGNORECASE):
                raise ValueError("Tower ambiguity: no independent card evidence; correct the annotation and its spans")
        _consistent(ext["reading_topic"], ext["topics_mentioned"], "tarot.reading_topic")
        _consistent(ext["reading_technique"], ext["analysis_methods"], "tarot.reading_technique")
        if not set(ext["spreads_used"]) <= set(ext["spreads_mentioned"]):
            raise ValueError("spreads_used must be a subset of spreads_mentioned")
        _consistent(ext["spread_used"], ext["spreads_used"], "tarot.spread_used")
        scoped_used = {item["spread"] for item in ext["spread_mentions"] if item["usage"] == "used"}
        if scoped_used != set(ext["spreads_used"]):
            raise ValueError("spreads_used must equal the scoped used spread mentions")
        if not {x["spread"] for x in ext["spread_mentions"]} <= set(ext["spreads_mentioned"]):
            raise ValueError("scoped spreads must occur in spreads_mentioned")
    relation_types = {row["canonical"] for row in vocab["relation_types"]}
    for relation in ext["relations"]:
        if relation["relation_type"] not in relation_types:
            raise ValueError("relation_type is not registered for this school")
        identities = {(p["entity_type"], p["name"], p["role"], p["location"]) for p in relation["participants"]}
        if len(identities) < 2:
            raise ValueError("relation requires at least two distinct participants/roles")
        contract = next(row for row in vocab["relation_types"] if row["canonical"] == relation["relation_type"])
        for entity, minimum in contract.get("minimum_distinct_entities", {}).items():
            if len({p["name"] for p in relation["participants"] if p["entity_type"] == entity}) < minimum:
                raise ValueError("relation participants do not meet the vocabulary entity contract")
        roles = contract.get("required_roles")
        if roles and sorted((p["role"], p["entity_type"]) for p in relation["participants"]) != sorted(roles.items()):
            raise ValueError("relation participants do not meet the vocabulary role contract")
        for participant in relation["participants"]:
            sections = vocab["relation_entities"].get(participant["entity_type"])
            if not sections or not any(
                participant["name"] in registry.get_valid_values(model.school, section) for section in sections
            ):
                raise ValueError("relation participant name/entity_type is not registered for this school")
    if not require_evidence:
        return
    data = model.model_dump()
    evidence = model.annotation_evidence
    by_path: dict[str, list[Any]] = {}
    scopes: dict[str, Any] = {}
    for item in evidence:
        try:
            value = value_at(data, item.field_path)
        except (IndexError, KeyError) as exc:
            raise ValueError(f"invalid evidence path: {item.field_path}") from exc
        if not (item.field_path.startswith(code + ".") or item.field_path == "topic"):
            raise ValueError("evidence must refer to the active school extension or topic")
        scope = item.scope.model_dump()
        previous = scopes.setdefault(item.scope.scope_id, scope)
        if previous != scope:
            raise ValueError("same scope_id has conflicting contexts")
        for span in item.spans:
            if span.end > len(model.chunk_text) or model.chunk_text[span.start : span.end] != span.quote:
                raise ValueError(f"evidence quote/offset mismatch: {item.field_path}")
        if value is None:
            if item.null_reason is None:
                raise ValueError(f"null requires a reason: {item.field_path}")
        elif item.null_reason is not None:
            raise ValueError("null_reason may only describe a null value")
        if item.basis == "source" and value is not None and not item.spans:
            raise ValueError(f"non-null source value requires evidence: {item.field_path}")
        if item.basis == "derived":
            if value is None:
                raise ValueError("derived fact must have a concrete verified value")
            if item.derivation_rule == "hexagram_palace_lookup" and item.field_path.endswith(".derived_palace"):
                parent = item.field_path.rsplit(".", 1)[0]
                if item.input_paths != [parent + ".hexagram_name"]:
                    raise ValueError("palace derivation must bind its own hexagram input")
            elif item.derivation_rule == "chart_time_context_lookup" and item.field_path == "ziwei.time_context":
                if (
                    item.input_paths != ["ziwei.chart_type"]
                    or value != vocab["chart_time_context_map"].get(ext["chart_type"])
                    or value is None
                ):
                    raise ValueError("time context derivation requires an unambiguous mapped chart type")
            else:
                raise ValueError("unregistered deterministic derivation")
        elif item.derivation_rule is not None or item.input_paths:
            raise ValueError("source evidence cannot carry an unexecuted derivation")
        if item.field_path.endswith(".derived_palace") and value is not None and item.basis != "derived":
            raise ValueError("derived palace cannot masquerade as a source statement")
        if item.scope.kind == "conditional" and (item.scope.condition or "") not in model.chunk_text:
            raise ValueError("conditional scope must retain a verbatim condition from this chunk")
        by_path.setdefault(item.field_path, []).append(item)
        if not isinstance(value, (dict, list)) and len({e.scope.scope_id for e in by_path[item.field_path]}) > 1:
            raise ValueError("scalar evidence cannot merge different scopes; use distinct scoped mentions")
    for path, value in _leaves(ext, code):
        # Derived coordinates are guaranteed by the single vocabulary surface map.
        if ".yao_mentioned." in path and path.endswith((".ordinal", ".polarity")):
            parent = path.rsplit(".", 1)[0]
            if value_at(data, parent + ".position") is not None:
                path = parent + ".position"
        entries = by_path.get(path, [])
        if value is not None:
            entries = entries or [e for p, rows in by_path.items() if path.startswith(p + ".") for e in rows]
        if not entries:
            raise ValueError(f"missing scoped field evidence: {path}")
        # Attributes of a scoped object cannot borrow evidence from another case.
        segments = path.split(".")
        for length in range(len(segments) - 1, 1, -1):
            parent = value_at(data, ".".join(segments[:length]))
            if isinstance(parent, dict) and parent.get("scope_id"):
                if any(e.scope.scope_id != parent["scope_id"] for e in entries):
                    raise ValueError(f"object/evidence scope mismatch: {path}")
                break
    if code == "ziwei":
        for i, mention in enumerate(ext["sihua_mentioned"]):
            path = f"ziwei.sihua_mentioned.{i}.star"
            if mention["star"] is None and not any(e.null_reason is not None for e in by_path.get(path, [])):
                raise ValueError(f"{path}: unnamed star requires an explicit null reason")
            if mention["star"] is None and (
                mention["scope_id"] is None
                or any(e.scope.scope_id != mention["scope_id"] for e in by_path.get(path, []))
            ):
                raise ValueError(f"{path}: unnamed star reason requires the same explicit object scope")
        if ext["time_mentions"] and ext["time_context"] is not None:
            source_scopes = {e.scope.scope_id for e in by_path.get("ziwei.time_context", [])}
            if source_scopes != {ext["time_mentions"][0]["scope_id"]}:
                raise ValueError("time_context projection must use its time mention scope")
    for i, term in enumerate(ext["unregistered_terms"]):
        from ._cjk import contains_cjk

        if contains_cjk(term["english_surface"]):
            raise ValueError("unregistered term english_surface must not contain CJK")
        rows = by_path.get(f"{code}.unregistered_terms.{i}", []) + by_path.get(
            f"{code}.unregistered_terms.{i}.surface", []
        )
        if not any(term["surface"] in span.quote for item in rows for span in item.spans):
            raise ValueError("unregistered surface must occur verbatim in its own evidence")
    if code in {"ziwei", "western"} and ext["chart_type"] is None and f"{code}.chart_type" not in by_path:
        raise ValueError("null chart_type requires an explicit process reason")
    for item in evidence:
        if item.basis == "derived":
            for path in item.input_paths:
                inputs = by_path.get(path, []) or [
                    e for p, rows in by_path.items() if path.startswith(p + ".") for e in rows
                ]
                if not any(e.basis == "source" and e.scope.scope_id == item.scope.scope_id for e in inputs):
                    raise ValueError("derived input requires source evidence in the same scope")
    if code == "ziwei" and ext["chart_type"] in vocab["chart_time_context_map"] and ext["time_context"] is not None:
        expected_time = vocab["chart_time_context_map"][ext["chart_type"]]
        chart_scopes = {e.scope.scope_id for e in by_path.get("ziwei.chart_type", [])}
        time_scopes = {e.scope.scope_id for e in by_path.get("ziwei.time_context", [])}
        if chart_scopes & time_scopes and ext["time_context"] != expected_time:
            raise ValueError("chart type and time context contradict within the same scope")
    for i, relation in enumerate(ext["relations"]):
        relation_scope = scopes.get(relation["scope_id"])
        if relation_scope is None or relation_scope["condition"] != relation["condition"]:
            raise ValueError(f"relations.{i}: scope/condition not bound to evidence")
    if code == "tarot":
        for i, mention in enumerate(ext["spread_mentions"]):
            path = f"tarot.spread_mentions.{i}"
            rows = [e for p, values in by_path.items() if p == path or p.startswith(path + ".") for e in values]
            if not any(e.usage == mention["usage"] for e in rows):
                raise ValueError("spread usage requires matching evidence usage")
