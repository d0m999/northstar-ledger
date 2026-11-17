"""Shared YAML vocabulary parsing utilities.

Pure-function module with zero external dependencies (only yaml, pathlib, typing).
Returns plain dicts and lists — consumers decide how to map to Pydantic models.

Public API:
    load_yaml(path)                      -> dict[str, Any]
    get_nested_value(data, path)         -> Any
    extract_values(entries, key)         -> list[str]
    build_alias_map(entries)             -> dict[str, str]

Constants:
    _YAML_LOADER   CSafeLoader when available (5-10x faster), else SafeLoader.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, cast

import yaml

# ---------------------------------------------------------------------------
# Use CSafeLoader when available for 5-10x faster YAML parsing.
# ---------------------------------------------------------------------------
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict.

    Args:
        path: Absolute path to the YAML file.

    Returns:
        Parsed YAML content as a dict.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"YAML file not found: {path}")

    with path.open(encoding="utf-8") as fh:
        loaded = yaml.load(fh, Loader=_YAML_LOADER)  # noqa: S506
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return cast(dict[str, Any], loaded)


def get_nested_value(data: dict[str, Any], path: str) -> Any:
    """Retrieve a value from a nested dict using a dot-separated path.

    Args:
        data: The root dictionary to navigate.
        path: Dot-separated key path, e.g. "stars.main_stars".

    Returns:
        The value at the given path, or None if any key is missing.
    """
    parts = path.split(".")
    current: Any = data

    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None

    return current


def extract_values(entries: Any, key: str) -> list[str]:
    """Extract all values for a given key from a list or dict-of-lists of entries.

    Args:
        entries: A list of dicts, a dict whose values are lists of dicts, or None.
        key:     The dict key to extract from each entry (e.g. "canonical").

    Returns:
        Flat list of extracted string values (skips entries missing the key).
    """
    if entries is None:
        return []

    result: list[str] = []

    if isinstance(entries, list):
        for item in entries:
            if isinstance(item, dict) and key in item:
                val = item[key]
                if isinstance(val, str) and val:
                    result.append(val)
    elif isinstance(entries, dict):
        for sub_list in entries.values():
            if isinstance(sub_list, list):
                for item in sub_list:
                    if isinstance(item, dict) and key in item:
                        val = item[key]
                        if isinstance(val, str) and val:
                            result.append(val)

    return result


def build_alias_map(entries: Any) -> dict[str, str]:
    """Build an alias → canonical mapping from vocabulary entries.

    Collects:
    - ``aliases`` list items
    - ``traditional`` field (if different from canonical)
    - ``english`` field (if different from canonical)
    - ``en_aliases`` list items (Phase 0.C: enables English alias → Chinese canonical lookup)

    Collision policy: **poison-both**.  When two entries claim the same alias surface
    form (across *any* field), neither canonical is placed in the map and a
    ``UserWarning`` is issued at ``stacklevel=4`` so callers see the warning at their
    own call site rather than inside this module.  The colliding alias is also
    recorded so that any *third* entry claiming the same surface form is silently
    skipped — ambiguous Pinyin (e.g. ``'Bi'`` → 水地比 vs 山火贲) must fall through
    to the clarification flow instead of resolving to an arbitrary canonical.

    Args:
        entries: A list of dicts, a dict whose values are lists of dicts, or None.

    Returns:
        Mapping of alias_string → canonical_string.
        The canonical itself is never a key in the map.
    """
    if entries is None:
        return {}

    alias_map: dict[str, str] = {}
    poisoned: set[str] = set()

    def _add_alias(alias: Any, canonical: str, field_name: str) -> None:
        """Add a single alias, honoring poison-both collision semantics.

        stacklevel=4 accounts for the call chain _add_alias → _process_entry →
        build_alias_map loop → external caller, so the warning is attributed to
        the user code that invoked build_alias_map() rather than this module.
        """
        if not isinstance(alias, str) or not alias or alias == canonical:
            return
        if alias in poisoned:
            return
        existing = alias_map.get(alias)
        if existing is None:
            alias_map[alias] = canonical
            return
        if existing == canonical:
            return
        warnings.warn(
            f"alias '{alias}' collides across entries: "
            f"{existing!r} vs {canonical!r} (both removed from alias_map, "
            f"field={field_name!r})",
            stacklevel=4,
        )
        del alias_map[alias]
        poisoned.add(alias)

    def _process_entry(entry: Any) -> None:
        if not isinstance(entry, dict):
            return

        canonical = entry.get("canonical")
        if not canonical:
            return

        for alias in entry.get("aliases") or []:
            _add_alias(alias, canonical, "aliases")

        _add_alias(entry.get("traditional"), canonical, "traditional")
        _add_alias(entry.get("english"), canonical, "english")

        for en_alias in entry.get("en_aliases") or []:
            _add_alias(en_alias, canonical, "en_aliases")

    if isinstance(entries, list):
        for entry in entries:
            _process_entry(entry)
    elif isinstance(entries, dict):
        for sub_list in entries.values():
            if isinstance(sub_list, list):
                for entry in sub_list:
                    _process_entry(entry)
            elif isinstance(sub_list, dict):
                _process_entry(sub_list)

    return alias_map


def build_zh_alias_map(entries: Any) -> dict[str, str]:
    """Like build_alias_map but only collects Chinese-side aliases.

    Collects:
    - ``aliases`` list items
    - ``traditional`` field

    Intentionally excludes ``english`` and ``en_aliases`` so that English
    surface forms do not pass membership validation for Chinese annotation fields.

    Args:
        entries: A list of dicts, a dict whose values are lists of dicts, or None.

    Returns:
        Mapping of alias_string → canonical_string (Chinese only).
    """
    if entries is None:
        return {}

    alias_map: dict[str, str] = {}
    poisoned: set[str] = set()

    def _add(alias: Any, canonical: str) -> None:
        if not isinstance(alias, str) or not alias or alias == canonical:
            return
        if alias in poisoned:
            return
        existing = alias_map.get(alias)
        if existing is None:
            alias_map[alias] = canonical
        elif existing != canonical:
            del alias_map[alias]
            poisoned.add(alias)

    def _process(entry: Any) -> None:
        if not isinstance(entry, dict):
            return
        canonical = entry.get("canonical")
        if not canonical:
            return
        for alias in entry.get("aliases") or []:
            _add(alias, canonical)
        _add(entry.get("traditional"), canonical)

    if isinstance(entries, list):
        for entry in entries:
            _process(entry)
    elif isinstance(entries, dict):
        for sub_list in entries.values():
            if isinstance(sub_list, list):
                for entry in sub_list:
                    _process(entry)
            elif isinstance(sub_list, dict):
                _process(sub_list)

    return alias_map


def validate_unique_term_ids(data: Any) -> None:
    """Reject duplicate vocabulary identities before a consumer can discard one."""
    seen: dict[str, str] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("id"), str) and isinstance(node.get("canonical"), str):
                key = node["id"]
                if key in seen:
                    raise ValueError(f"duplicate vocabulary term_id {key}: {seen[key]} / {node['canonical']}")
                seen[key] = node["canonical"]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
