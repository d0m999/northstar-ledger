"""Payload Projector — 注入 vocab 版本戳到 AnnotatedChunkV2 payload。

Batch B 实现范围:
- 版本戳注入（vocab_version / school_vocab_version / annotation_version）
- 基础 model_dump(exclude_none=True) 输出
- chart_type / analysis_type 字段的 migration 前向兼容解析（T1.8: topics → analysis_type）
- term_id 映射（T1.11: Batch C）

T1.11 term_id 映射策略:
- 对各流派 mention 类字段做 canonical → term_id rewrite
- 先过 migrator.resolve_value() 兼容历史漂移，再查 term_id
- 映射未命中: 保留原 canonical + log warning + payload 加 _unmapped_terms: list[str]
- houses_mentioned (int) 不映射

使用方式::

    from astrology_corpus.annotation.payload_projector import project_to_payload
    payload = project_to_payload(chunk)  # 返回 Qdrant-ready dict
"""

from __future__ import annotations

import logging
from typing import Any, Final

from . import ANNOTATION_VERSION
from .schemas import AnnotatedChunkV2
from .vocab_loader import get_registry
from .vocab_migrator import get_migrator

_log = logging.getLogger(__name__)


def _truncate(v: Any, n: int = 30) -> str:
    """将任意值 repr 截断到 n 字符，超出加省略号。"""
    s = repr(v)
    return s if len(s) <= n else s[:n] + "..."


# 流派代码 → AnnotatedChunkV2 扩展字段名映射（模块级常量）
_SCHOOL_EXT_KEY: Final[dict[str, str]] = {
    "紫微": "ziwei",
    "六爻": "liuyao",
    "塔罗": "tarot",
    "西占": "western",
}


# ═══════════════════════════════════════════════════════════════════════════════
# term_id 映射配置
# ═══════════════════════════════════════════════════════════════════════════════

# school → 字段名 → vocab field_path（list[str] 类字段，直接映射）
_SIMPLE_MENTION_FIELD_PATHS: dict[str, dict[str, str]] = {
    "紫微": {
        "stars_mentioned": "stars",
        "palaces_mentioned": "palaces.twelve_palaces",
        "star_pairs_mentioned": "star_pairs",
        "star_interactions_mentioned": "star_interactions.tonggong_combinations",
        "topics_mentioned": "topics",
    },
    "西占": {
        "signs_mentioned": "zodiac_signs",
        "elements_mentioned": "elements",
        "modalities_mentioned": "modalities",
        "dignities_mentioned": "dignities.essential_dignities",
        "fixed_stars_mentioned": "fixed_stars",
        "analysis_type_mentioned": "analysis_type",
    },
    "塔罗": {
        "suits_mentioned": "suits",
        "elements_mentioned": "elements",
        "spreads_mentioned": "spreads",
        "positions_mentioned": "positions",
        "reading_techniques_mentioned": "reading_techniques",
    },
    "六爻": {
        "six_relatives_mentioned": "six_relatives",
        "trigrams_mentioned": "trigrams",
    },
}


def _apply_term_id_to_list(
    values: list[str],
    term_id_map: dict[str, str],
    school: str,
    field: str,
    unmapped: list[str],
) -> list[str]:
    """将 list[str] 中的 canonical 替换为 term_id。

    未命中的 canonical 保留原值并记录到 unmapped 列表。
    """
    result = []
    for val in values:
        if val in term_id_map:
            result.append(term_id_map[val])
        else:
            _log.warning("term_id 映射未命中: school=%r field=%r value=%s", school, field, _truncate(val))
            unmapped.append(val)
            result.append(val)  # 保留原值
    return result


def _rewrite_simple_mention_fields(
    ext_dict: dict[str, Any],
    school: str,
    registry: Any,
    migrator: Any,
    unmapped: list[str],
) -> dict[str, Any]:
    """对 ext_dict 中的 list[str] mention 字段做 canonical → term_id rewrite。

    # TODO(Batch D+): 若未来新增直接读 JSONL 旁路 schema 的 projector 入口，
    # 需调 migrator.resolve_field(school, old_field) 处理历史字段名。
    # 当前路径全部走 AnnotatedChunkV2.model_dump，schema alias 已覆盖 rename。
    """
    field_paths = _SIMPLE_MENTION_FIELD_PATHS.get(school, {})
    if not field_paths:
        return ext_dict

    result = dict(ext_dict)
    for field, vocab_path in field_paths.items():
        raw_vals = result.get(field)
        if not isinstance(raw_vals, list) or not raw_vals:
            continue

        # 先过 migrator 兼容历史漂移
        resolved: list[str] = []
        for v in raw_vals:
            r = migrator.resolve_value(school, field, v)
            resolved.append(r if r is not None else v)

        # 查 term_id map
        id_map = registry.get_term_id_map(school, vocab_path)
        if not id_map:
            continue

        result[field] = _apply_term_id_to_list(resolved, id_map, school, field, unmapped)

    return result


def _rewrite_ziwei_nested_fields(
    ext_dict: dict[str, Any],
    school: str,
    registry: Any,
    migrator: Any,
    unmapped: list[str],
) -> dict[str, Any]:
    """紫微嵌套字段: patterns_mentioned[*].pattern_name, sihua_mentioned[*].star/.palace。"""
    result = dict(ext_dict)

    # patterns_mentioned[*].pattern_name
    patterns = result.get("patterns_mentioned")
    if patterns and isinstance(patterns, list):
        pattern_id_map = registry.get_term_id_map("紫微", "patterns")
        new_patterns = []
        for p in patterns:
            if isinstance(p, dict) and "pattern_name" in p:
                pn = p["pattern_name"]
                resolved_pn = migrator.resolve_value(school, "patterns_mentioned", pn)
                pn_to_map = resolved_pn if resolved_pn is not None else pn
                new_p = dict(p)
                if pn_to_map in pattern_id_map:
                    new_p["pattern_name"] = pattern_id_map[pn_to_map]
                else:
                    _log.warning("pattern_name term_id 未命中: %r", pn_to_map)
                    unmapped.append(pn_to_map)
                new_patterns.append(new_p)
            else:
                new_patterns.append(p)
        result["patterns_mentioned"] = new_patterns

    # sihua_mentioned[*].star and .palace
    sihua = result.get("sihua_mentioned")
    if sihua and isinstance(sihua, list):
        star_id_map = registry.get_term_id_map("紫微", "stars")
        palace_id_map = registry.get_term_id_map("紫微", "palaces.twelve_palaces")
        new_sihua = []
        for s in sihua:
            if isinstance(s, dict):
                ns = dict(s)
                star_val = ns.get("star")
                if star_val:
                    resolved_star = migrator.resolve_value(school, "sihua_mentioned.star", star_val)
                    to_map = resolved_star if resolved_star is not None else star_val
                    if to_map in star_id_map:
                        ns["star"] = star_id_map[to_map]
                    else:
                        unmapped.append(to_map)

                palace_val = ns.get("palace")
                if palace_val:
                    resolved_pal = migrator.resolve_value(school, "sihua_mentioned.palace", palace_val)
                    to_map_pal = resolved_pal if resolved_pal is not None else palace_val
                    if to_map_pal in palace_id_map:
                        ns["palace"] = palace_id_map[to_map_pal]
                    else:
                        unmapped.append(to_map_pal)

                new_sihua.append(ns)
            else:
                new_sihua.append(s)
        result["sihua_mentioned"] = new_sihua

    return result


def _rewrite_liuyao_nested_fields(
    ext_dict: dict[str, Any],
    school: str,
    registry: Any,
    migrator: Any,
    unmapped: list[str],
) -> dict[str, Any]:
    """六爻嵌套字段: hexagrams_mentioned[*].hexagram_name。"""
    result = dict(ext_dict)

    hexagrams = result.get("hexagrams_mentioned")
    if hexagrams and isinstance(hexagrams, list):
        # 六爻 hexagrams 以 palace 分组，尝试合并所有 palace 的 term_id map
        all_hexagram_id_map: dict[str, str] = {}
        vocab = registry.load(school)
        hex_section = vocab.get("hexagrams", {})
        if isinstance(hex_section, dict):
            for palace_name, _entries in hex_section.items():
                id_map = registry.get_term_id_map(school, f"hexagrams.{palace_name}")
                all_hexagram_id_map.update(id_map)

        new_hexagrams = []
        for h in hexagrams:
            if isinstance(h, dict) and "hexagram_name" in h:
                hn = h["hexagram_name"]
                resolved_hn = migrator.resolve_value(school, "hexagrams_mentioned.hexagram_name", hn)
                to_map = resolved_hn if resolved_hn is not None else hn
                nh = dict(h)
                if to_map in all_hexagram_id_map:
                    nh["hexagram_name"] = all_hexagram_id_map[to_map]
                else:
                    _log.warning("hexagram_name term_id 未命中: %r", to_map)
                    unmapped.append(to_map)
                new_hexagrams.append(nh)
            else:
                new_hexagrams.append(h)
        result["hexagrams_mentioned"] = new_hexagrams

    return result


def _build_western_planets_id_map(registry: Any, school: str) -> dict[str, str]:
    """Western planets (扁平化后单层 list[40]) 的 canonical→term_id 映射。

    T4.H1 (2.9.0) 扁平化后 planets 为 list，category 字段承担 9 组语义。
    返回空字典表示 vocab 中无 planets 段或段为空。
    """
    raw_map = registry.get_term_id_map(school, "planets")
    if not isinstance(raw_map, dict):
        return {}
    return {str(key): str(value) for key, value in raw_map.items()}


def _build_western_aspects_id_map(registry: Any, school: str) -> dict[str, str]:
    """合并 western aspects 所有子段的 canonical→term_id 映射。

    迭代 vocab["aspects"] 的所有子段键并合并各 get_term_id_map 结果。
    返回空字典表示 vocab 中无 aspects 段或子段均为空。
    """
    aspect_id_map: dict[str, str] = {}
    vocab = registry.load(school)
    aspects_section = vocab.get("aspects", {})
    if isinstance(aspects_section, dict):
        for sub_key in aspects_section:
            sub_map = registry.get_term_id_map(school, f"aspects.{sub_key}")
            aspect_id_map.update(sub_map)
    return aspect_id_map


def _rewrite_western_nested_fields(
    ext_dict: dict[str, Any],
    school: str,
    registry: Any,
    migrator: Any,
    unmapped: list[str],
) -> dict[str, Any]:
    """西占嵌套字段: planets_mentioned[*].planet/.sign, retrograde_planets。"""
    result = dict(ext_dict)

    planets_id_map = _build_western_planets_id_map(registry, school)

    # zodiac_signs id_map（用于 .sign 字段）
    sign_id_map = registry.get_term_id_map(school, "zodiac_signs")

    # planets_mentioned[*].planet 和 .sign
    planets_mentioned = result.get("planets_mentioned")
    if planets_mentioned and isinstance(planets_mentioned, list):
        new_planets = []
        for pm in planets_mentioned:
            if not isinstance(pm, dict):
                new_planets.append(pm)
                continue
            npm = dict(pm)

            # .planet
            planet_val = npm.get("planet")
            if planet_val and isinstance(planet_val, str):
                resolved = migrator.resolve_value(school, "planets_mentioned.planet", planet_val)
                to_map = resolved if resolved is not None else planet_val
                if to_map in planets_id_map:
                    npm["planet"] = planets_id_map[to_map]
                else:
                    _log.warning("planets term_id 未命中: %r", to_map)
                    unmapped.append(to_map)

            # .sign
            sign_val = npm.get("sign")
            if sign_val and isinstance(sign_val, str):
                resolved_sign = migrator.resolve_value(school, "planets_mentioned.sign", sign_val)
                to_map_sign = resolved_sign if resolved_sign is not None else sign_val
                if to_map_sign in sign_id_map:
                    npm["sign"] = sign_id_map[to_map_sign]
                else:
                    _log.warning("zodiac_sign term_id 未命中: %r", to_map_sign)
                    unmapped.append(to_map_sign)

            # .house (int) 和 .is_retrograde (bool) 跳过，不做 term_id 映射
            new_planets.append(npm)
        result["planets_mentioned"] = new_planets

    # aspects_mentioned[*].planet1/.planet2/.aspect_type
    aspects_mentioned = result.get("aspects_mentioned")
    if aspects_mentioned and isinstance(aspects_mentioned, list):
        aspect_id_map = _build_western_aspects_id_map(registry, school)

        new_aspects = []
        for am in aspects_mentioned:
            if not isinstance(am, dict):
                new_aspects.append(am)
                continue
            nam = dict(am)

            for planet_field in ("planet1", "planet2"):
                pv = nam.get(planet_field)
                if pv and isinstance(pv, str):
                    resolved = migrator.resolve_value(school, f"aspects_mentioned.{planet_field}", pv)
                    to_map = resolved if resolved is not None else pv
                    if to_map in planets_id_map:
                        nam[planet_field] = planets_id_map[to_map]
                    else:
                        _log.warning("aspects %s term_id 未命中: %r", planet_field, to_map)
                        unmapped.append(to_map)

            aspect_type_val = nam.get("aspect_type")
            if aspect_type_val and isinstance(aspect_type_val, str):
                resolved_at = migrator.resolve_value(school, "aspects_mentioned.aspect_type", aspect_type_val)
                to_map_at = resolved_at if resolved_at is not None else aspect_type_val
                if to_map_at in aspect_id_map:
                    nam["aspect_type"] = aspect_id_map[to_map_at]
                else:
                    _log.warning("aspect_type term_id 未命中: %r", to_map_at)
                    unmapped.append(to_map_at)

            new_aspects.append(nam)
        result["aspects_mentioned"] = new_aspects

    # retrograde_planets: list[str] — 用 planets 合并 map
    retro = result.get("retrograde_planets")
    if retro and isinstance(retro, list):
        new_retro = []
        for val in retro:
            if not isinstance(val, str):
                new_retro.append(val)
                continue
            resolved = migrator.resolve_value(school, "retrograde_planets", val)
            to_map = resolved if resolved is not None else val
            if to_map in planets_id_map:
                new_retro.append(planets_id_map[to_map])
            else:
                _log.warning("retrograde_planets term_id 未命中: %r", to_map)
                unmapped.append(to_map)
                new_retro.append(val)
        result["retrograde_planets"] = new_retro

    return result


def _rewrite_tarot_nested_fields(
    ext_dict: dict[str, Any],
    school: str,
    registry: Any,
    migrator: Any,
    unmapped: list[str],
) -> dict[str, Any]:
    """塔罗嵌套字段: cards_mentioned[*].card_name。"""
    result = dict(ext_dict)

    cards = result.get("cards_mentioned")
    if cards and isinstance(cards, list):
        # 合并 major_arcana + minor_arcana.*
        all_card_id_map: dict[str, str] = {}
        all_card_id_map.update(registry.get_term_id_map(school, "major_arcana"))
        vocab = registry.load(school)
        minor = vocab.get("minor_arcana", {})
        if isinstance(minor, dict):
            for suit_name in minor:
                id_map = registry.get_term_id_map(school, f"minor_arcana.{suit_name}")
                all_card_id_map.update(id_map)

        new_cards = []
        for c in cards:
            if isinstance(c, dict) and "card_name" in c:
                cn = c["card_name"]
                resolved_cn = migrator.resolve_value(school, "cards_mentioned.card_name", cn)
                to_map = resolved_cn if resolved_cn is not None else cn
                nc = dict(c)
                if to_map in all_card_id_map:
                    nc["card_name"] = all_card_id_map[to_map]
                else:
                    _log.warning("card_name term_id 未命中: %r", to_map)
                    unmapped.append(to_map)
                new_cards.append(nc)
            else:
                new_cards.append(c)
        result["cards_mentioned"] = new_cards

    return result


def project_to_payload(chunk: AnnotatedChunkV2) -> dict[str, Any]:
    """将 AnnotatedChunkV2 投影为 Qdrant-ready payload dict。

    注入版本戳字段:
    - vocab_version: 流派 vocab _meta.version（字符串）
    - school_vocab_version: 同上（Batch B 中与 vocab_version 相同，Phase 2 可分离）
    - annotation_version: ANNOTATION_VERSION 常量

    T1.11: 对 mention 类字段做 canonical → term_id rewrite。
    映射未命中时加 _unmapped_terms 字段并 log warning。

    Args:
        chunk: 待投影的标注 chunk

    Returns:
        Qdrant payload dict（exclude_none=True + 版本戳 + term_id rewrite）
    """
    registry = get_registry()
    migrator = get_migrator()

    # 基础序列化：排除 None 字段
    base = chunk.model_dump(exclude_none=True)

    # 读取流派 vocab 版本
    school_ver: str = "unknown"
    try:
        school_meta = registry.get_meta(chunk.school)
        if isinstance(school_meta, dict):
            v = school_meta.get("version")
            if v is not None:
                school_ver = str(v)
    except Exception as exc:
        _log.warning("无法读取流派 %r 的 vocab 版本: %s", chunk.school, exc)

    # 注入版本戳（覆盖 model_dump 中可能的 None）
    base["vocab_version"] = school_ver
    base["school_vocab_version"] = school_ver
    base["annotation_version"] = chunk.annotation_version or ANNOTATION_VERSION

    # T1.11: term_id 映射
    school = chunk.school
    unmapped: list[str] = []

    # 找到流派对应的扩展字段键
    ext_key = _SCHOOL_EXT_KEY.get(school)

    if ext_key and ext_key in base:
        ext_dict = base[ext_key]
        if isinstance(ext_dict, dict):
            # 1. list[str] 简单字段
            ext_dict = _rewrite_simple_mention_fields(ext_dict, school, registry, migrator, unmapped)
            # 2. 嵌套字段（按流派）
            if school == "紫微":
                ext_dict = _rewrite_ziwei_nested_fields(ext_dict, school, registry, migrator, unmapped)
            elif school == "六爻":
                ext_dict = _rewrite_liuyao_nested_fields(ext_dict, school, registry, migrator, unmapped)
            elif school == "西占":
                ext_dict = _rewrite_western_nested_fields(ext_dict, school, registry, migrator, unmapped)
            elif school == "塔罗":
                ext_dict = _rewrite_tarot_nested_fields(ext_dict, school, registry, migrator, unmapped)
            base[ext_key] = ext_dict

    if unmapped:
        base["_unmapped_terms"] = list(dict.fromkeys(unmapped))

    # T2.2: 西占 chunk-side en_alias_types gate
    # 将 TermRegistry 中西占 terms 的 contextual en_aliases 投影到顶层
    # 供 retriever 下游做 query-time 折扣过滤，与 query-side planner 双侧对齐
    if school == "西占":
        contextual_list = _collect_western_contextual_en_aliases(registry)
        if contextual_list:
            base["en_alias_types_contextual"] = contextual_list

    # D01–D13: preserve scoped nulls and build independent evidence-backed signals.
    from .semantic_projection import project_semantic_metadata

    base.update(project_semantic_metadata(chunk, registry))
    if school == "西占" and chunk.western is not None:
        base["western_chart_type"] = chunk.western.chart_type

    return base


# M5: module-level cache for _collect_western_contextual_en_aliases
# key = id(registry)，value = frozenset[str]（不可变，线程安全读）
_contextual_en_cache: dict[int, frozenset[str]] = {}


def _collect_western_contextual_en_aliases(registry: Any) -> list[str]:
    """收集西占 vocab 中所有 contextual en_aliases（小写）。

    供 chunk payload 的 en_alias_types_contextual 字段使用（T2.2 chunk-side gate）。
    遍历 western_vocabulary.yaml，提取 en_alias_types 标注为 contextual 的词。
    结果按 registry 实例 id 缓存，相同 registry 只遍历一次。
    """
    cache_key = id(registry)
    cached = _contextual_en_cache.get(cache_key)
    if cached is not None:
        return sorted(cached)

    contextual_set: set[str] = set()
    try:
        vocab = registry.load("西占")
        _walk_vocab_for_contextual_en(vocab, contextual_set)
    except (OSError, KeyError, TypeError, AttributeError) as exc:
        _log.warning("收集西占 contextual en_aliases 失败: %s", exc)

    frozen = frozenset(contextual_set)
    _contextual_en_cache[cache_key] = frozen
    return sorted(frozen)


def _walk_vocab_for_contextual_en(
    node: Any,
    result: set[str],
    depth: int = 0,
) -> None:
    """递归遍历 vocab 树，收集 en_alias_types=contextual 的 en_aliases 值。"""
    if depth > 15 or not isinstance(node, dict):
        return

    # 叶节点：检查 en_aliases / en_alias_types
    en_aliases = node.get("en_aliases", [])
    en_alias_types = node.get("en_alias_types", {})
    if isinstance(en_aliases, list) and isinstance(en_alias_types, dict):
        for alias in en_aliases:
            alias_type = en_alias_types.get(alias, "contextual")  # 默认 contextual
            if alias_type == "contextual":
                result.add(alias.lower())

    # 递归子节点
    for val in node.values():
        if isinstance(val, dict):
            _walk_vocab_for_contextual_en(val, result, depth + 1)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    _walk_vocab_for_contextual_en(item, result, depth + 1)
