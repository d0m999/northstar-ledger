"""Deterministic review signals, not a semantic acceptance oracle."""

from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from .output_validation import _entries
from .schemas import SCHOOL_TO_CODE
from .vocab_loader import VocabularyRegistry, get_registry


def _strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {s for child in value.values() for s in _strings(child)}
    if isinstance(value, list):
        return {s for child in value for s in _strings(child)}
    return set()


def review_annotations(rows: list[dict[str, Any]], registry: VocabularyRegistry | None = None) -> dict[str, Any]:
    """Report missing term candidates, English anchors, and batch distributions.

    A surface hit can be negated, quoted, or ambiguous. It must be adjudicated
    from source evidence; no warning authorizes automatic label insertion.
    """
    registry = registry or get_registry()
    findings = []
    batches = []
    for row in rows:
        school = str(row.get("school", ""))
        code = SCHOOL_TO_CODE.get(school, "generic")
        extension = row.get(code) or {}
        if school != "紫微":
            continue
        structured = _strings(extension)
        text = row.get("chunk_text", "")
        english = " ".join([row.get("summary_en", ""), *row.get("keywords_en", [])]).lower()
        for section in ("stars", "palaces", "patterns", "star_groups"):
            for term in _entries(registry.get_field_metadata(school, section), "canonical"):
                names = {term["canonical"], term.get("traditional", ""), *term.get("aliases", [])} - {""}
                hits = [name for name in names if len(name) >= 2 and name in text]
                if hits and not (names & structured):
                    findings.append(
                        {
                            "chunk_id": row["chunk_id"],
                            "code": "source_term_uncovered",
                            "term": term["canonical"],
                            "surfaces": sorted(hits),
                        }
                    )
                if names & structured:
                    anchors = [term.get("english", ""), *term.get("en_aliases", [])]
                    if not any(anchor and anchor.lower() in english for anchor in anchors):
                        findings.append(
                            {
                                "chunk_id": row["chunk_id"],
                                "code": "english_anchor_review",
                                "term": term["canonical"],
                                "expected": anchors,
                            }
                        )
    source_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("source_file", "")), str(row.get("school", "")))
        source_groups.setdefault(key, []).append(row)
    for (source_file, school), source_rows in source_groups.items():
        for start in range(0, len(source_rows), 50):
            batch = source_rows[start : start + 50]
            methods = sum(
                bool((r.get(SCHOOL_TO_CODE.get(str(r.get("school", "")), "generic")) or {}).get("analysis_methods"))
                for r in batch
            )
            batches.append(
                {
                    "source_file": source_file,
                    "school": school,
                    "first_id": batch[0]["chunk_id"],
                    "last_id": batch[-1]["chunk_id"],
                    "count": len(batch),
                    "content_types": dict(Counter(r.get("content_type") for r in batch)),
                    "methods_nonempty": methods,
                }
            )
    return {"semantic_acceptance": False, "findings_are_review_signals": True, "findings": findings, "batches": batches}


def shared_span_candidates(rows: list[dict[str, Any]], minimum_characters: int = 120) -> list[dict[str, Any]]:
    """Read-only cross-source overlap candidates. Never treats overlap as equivalence."""
    findings = []
    texts = [re.sub(r"\s+", "", row.get("chunk_text", "")) for row in rows]
    for i, first in enumerate(rows):
        for j in range(i + 1, len(rows)):
            second = rows[j]
            if first.get("source_file") == second.get("source_file") or first.get("school") != second.get("school"):
                continue
            match = SequenceMatcher(None, texts[i], texts[j], autojunk=False).find_longest_match()
            if match.size >= minimum_characters:
                findings.append(
                    {
                        "first_id": first["chunk_id"],
                        "second_id": second["chunk_id"],
                        "shared_characters": match.size,
                        "full_text_equal": texts[i] == texts[j],
                        "action": "review_unique_context_before_grouping",
                    }
                )
    return findings
