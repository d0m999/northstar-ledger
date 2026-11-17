"""受控词表加载器 — 从 YAML 加载词表并生成 LLM 约束。

VocabularyRegistry 类提供:
- 按流派加载词表
- 获取某字段的所有合法值
- 获取别名 → 标准名映射
- 生成 LLM Prompt 的 Enum 约束文本
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from astrology_shared.vocab_parser import (
    build_alias_map as _build_alias_map,
    build_zh_alias_map as _build_zh_alias_map,
    extract_values as _extract_values,
    get_nested_value as _get_nested_value,
    load_yaml as _load_yaml,
    validate_unique_term_ids,
)

_log = logging.getLogger(__name__)

_VOCAB_HOT_RELOAD_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})

# 支持的流派类型 —— runtime 校验由 load() 中的 SCHOOL_FILE_MAP 查表兜底。
# 不使用 typing.Literal，避免违反 Vocab SoT Contract 的"零 Literal 硬编码"原则。
# 语义上等价于 Literal["紫微", "六爻", "塔罗", "西占"]，但走 runtime 校验（SCHOOL_FILE_MAP）。
SchoolType = str  # type alias；合法值集合由 SCHOOL_FILE_MAP.keys() 动态读取

# 流派到文件名的映射
SCHOOL_FILE_MAP: dict[str, str] = {
    "紫微": "ziwei_vocabulary.yaml",
    "六爻": "liuyao_vocabulary.yaml",
    "塔罗": "tarot_vocabulary.yaml",
    "西占": "western_vocabulary.yaml",
}

# 默认词表目录 (相对于本文件)
DEFAULT_VOCAB_DIR = Path(__file__).parent.parent / "dictionaries"


def _collect_english_values(entries: Any, out: dict[str, str]) -> None:
    """Populate *out* with {canonical: english} pairs from vocabulary entries.

    Only entries that have both a non-empty ``canonical`` and a non-empty
    ``english`` field are included.  Entries without ``english`` are silently
    skipped so callers can fall back to canonical-only display.

    Supports the same two entry shapes as ``build_alias_map``:
    - ``list[dict]``
    - ``dict[str, list[dict]]``
    """

    def _process(entry: Any) -> None:
        if not isinstance(entry, dict):
            return
        canonical = entry.get("canonical")
        english = entry.get("english")
        if isinstance(canonical, str) and canonical and isinstance(english, str) and english:
            out[canonical] = english

    if isinstance(entries, list):
        for entry in entries:
            _process(entry)
    elif isinstance(entries, dict):
        for sub in entries.values():
            if isinstance(sub, list):
                for entry in sub:
                    _process(entry)


class VocabularyRegistry:
    """受控词表注册中心 — 加载 YAML 并生成 LLM 约束。

    Example:
        >>> registry = VocabularyRegistry()
        >>> registry.get_valid_values("紫微", "palaces.twelve_palaces")
        ['命宫', '兄弟宫', '夫妻宫', ...]
        >>> registry.get_aliases_map("紫微", "stars.main_stars")
        {'紫微星': '紫微', '帝星': '紫微', ...}
    """

    def __init__(self, vocab_dir: Path | None = None) -> None:
        """初始化词表注册中心。

        Args:
            vocab_dir: 词表目录路径。默认使用 dictionaries/ 目录。

        Note:
            ``hot_reload_enabled`` 是构造时从环境变量 ``VOCAB_HOT_RELOAD`` 读取的只读位，
            后续修改环境变量不会影响已创建的实例；需重建实例才能感知变化。

            ``hot_reload_enabled`` 现在是 ``reload()`` 的运行时 gate（Phase 2 B3 生效）：
              - prod（默认 False）：``reload()`` 走 no-op 分支，缓存保持不变，
                防止并发 YAML 读取导致语义漂移；
              - dev（``VOCAB_HOT_RELOAD=1`` 等真值）：``reload()`` 清空缓存并重新读 YAML。
              - ``load_all()`` 始终幂等，不受 gate 影响；原语 ``reload(force=True)``
                供内部工具绕过 gate 使用（如 fallback_warned 测试场景）。
        """
        resolved_dir = (vocab_dir or DEFAULT_VOCAB_DIR).resolve()
        self.vocab_dir = resolved_dir
        if vocab_dir is not None and not resolved_dir.is_dir():
            _log.warning("词表目录不存在，构造继续（所有 school 将 skip）: %s", resolved_dir)
        self._cache: dict[str, dict[str, Any]] = {}
        # key = (school, field_path, canonical)；全进程只 warn 一次，reload 后重置
        self._fallback_warned: set[tuple[str, str, str]] = set()
        self.hot_reload_enabled: bool = os.getenv("VOCAB_HOT_RELOAD", "").strip().lower() in _VOCAB_HOT_RELOAD_TRUTHY
        self.load_all()

    def load(self, school: SchoolType) -> dict[str, Any]:
        """加载指定流派的词表。

        Args:
            school: 流派名称 ("紫微", "六爻", "塔罗", "西占")

        Returns:
            完整词表字典

        Raises:
            FileNotFoundError: 词表文件不存在
            ValueError: 不支持的流派
        """
        if school not in SCHOOL_FILE_MAP:
            valid_schools = ", ".join(SCHOOL_FILE_MAP.keys())
            raise ValueError(f"不支持的流派: {school}。支持的流派: {valid_schools}")

        if school not in self._cache:
            path = self.vocab_dir / SCHOOL_FILE_MAP[school]
            data = _load_yaml(path)
            validate_unique_term_ids(data)
            self._cache[school] = data

        return self._cache[school]

    def load_all(self) -> None:
        """预加载所有已知流派的词表。

        对于目录中缺失的 YAML 文件，记录 warning 并跳过，不抛异常。
        此方法幂等：已在 _cache 中的 school 不会重复读取。
        """
        for school in SCHOOL_FILE_MAP:
            try:
                self.load(school)
            except FileNotFoundError:
                _log.warning(
                    "词表文件缺失，跳过 school=%s（路径: %s/%s）",
                    school,
                    self.vocab_dir,
                    SCHOOL_FILE_MAP[school],
                )

    def reload(self, *, force: bool = False) -> None:
        """清空缓存并重新加载所有词表（dev opt-in gate）。

        prod 环境（默认 ``VOCAB_HOT_RELOAD`` 未设置）下本方法走 no-op 分支，
        保持缓存不变 —— 防止并发 YAML 读取造成语义漂移（架构师 R1 决策）。
        dev 环境（``VOCAB_HOT_RELOAD=1`` 等真值）下实际清空 ``_cache`` +
        ``_fallback_warned`` 并重新 ``load_all()``。

        Args:
            force: 绕过 gate 强制 reload。供内部工具（如测试、rag_debugger
                手动触发）使用；生产代码不应传 ``force=True``。

        Note:
            ``load_all()`` 自身始终幂等，不受本 gate 影响。调用方始终可安全调用
            本方法而不抛异常；prod 下调用方拿到的是"缓存维持"的 no-op 语义。
        """
        if not (self.hot_reload_enabled or force):
            _log.debug("reload() skipped: prod 模式（VOCAB_HOT_RELOAD 未设置），缓存保持不变")
            return
        self._cache.clear()
        self._fallback_warned.clear()
        self.load_all()

    def get_valid_values(
        self,
        school: SchoolType,
        field_path: str,
        *,
        key: str = "canonical",
        value_shape: str = "list",
    ) -> list[str]:
        """获取某字段的所有合法值 (canonical 名称)。

        Args:
            school: 流派名称
            field_path: 字段路径，支持点分隔的嵌套路径
                       例如 "stars.main_stars", "palaces.twelve_palaces"
            key: 提取的键名，默认 "canonical"
            value_shape: 词表结构类型。
                "list" (默认) — 走现有 _extract_values 路径；
                "dict_of_dicts" — 词表该字段是 dict[str, dict]，提取每个 entry 的 canonical 字段；
                "dict_of_lists" — 词表该字段是 dict[str, list[entry]]，合并所有子 list 后按 key 提取。
                    历史用途: western planets 在 2.8.0 及之前版本使用此 shape；
                    2.9.0 扁平化后无活跃消费者，保留 shape 值用于未来可能的新 vocab。

        Returns:
            合法值列表

        Example:
            >>> registry.get_valid_values("紫微", "stars.main_stars")
            ['紫微', '天机', '太阳', ...]
            >>> registry.get_valid_values("塔罗", "major_arcana")
            ['愚者', '魔术师', ...]
            >>> registry.get_valid_values("塔罗", "elements", value_shape="dict_of_dicts")
            ['火', '水', '风', '土']
        """
        vocab = self.load(school)
        entries = _get_nested_value(vocab, field_path)

        if entries is None:
            return []

        if value_shape == "dict_of_dicts":
            if isinstance(entries, dict):
                return [v["canonical"] for v in entries.values() if isinstance(v, dict) and "canonical" in v]
            return []

        if value_shape == "dict_of_lists":
            if isinstance(entries, dict):
                merged: list[str] = []
                for sub in entries.values():
                    if isinstance(sub, list):
                        merged.extend(_extract_values(sub, key))
                return merged
            return []

        return _extract_values(entries, key)

    def get_int_values(
        self,
        school: SchoolType,
        field_path: str,
        *,
        key: str = "number",
    ) -> list[int]:
        """提取词表字段中的整数值 (如 houses[*].number)。

        Args:
            school: 流派名称
            field_path: 字段路径
            key: 提取的整数键名，默认 "number"

        Returns:
            整数值列表

        Example:
            >>> registry.get_int_values("西占", "houses")
            [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        """
        vocab = self.load(school)
        entries = _get_nested_value(vocab, field_path)

        if entries is None:
            return []

        if isinstance(entries, list):
            result = []
            for entry in entries:
                if isinstance(entry, dict) and key in entry:
                    val = entry[key]
                    if isinstance(val, int):
                        result.append(val)
            return result

        return []

    def get_aliases_map(
        self,
        school: SchoolType,
        field_path: str,
    ) -> dict[str, str]:
        """获取别名 → 标准名映射表。

        包含:
        - aliases 字段中的所有别名
        - traditional 字段 (繁体)
        - english 字段 (英文，如果存在)

        Args:
            school: 流派名称
            field_path: 字段路径

        Returns:
            别名到 canonical 名称的映射

        Example:
            >>> registry.get_aliases_map("紫微", "stars.main_stars")
            {'紫微星': '紫微', '帝星': '紫微', '天機': '天机', ...}
        """
        vocab = self.load(school)
        entries = _get_nested_value(vocab, field_path)

        if entries is None:
            return {}

        return _build_alias_map(entries)

    def get_zh_aliases_map(
        self,
        school: SchoolType,
        field_path: str,
    ) -> dict[str, str]:
        """获取中文别名 → 标准名映射（仅 aliases + traditional，不含英文字段）。"""
        vocab = self.load(school)
        entries = _get_nested_value(vocab, field_path)
        if entries is None:
            return {}
        return _build_zh_alias_map(entries)

    def normalize_term(
        self,
        school: SchoolType,
        field_path: str,
        term: str,
    ) -> str | None:
        """将术语归一化为标准名称。

        如果术语是标准名称，直接返回；
        如果是别名，返回对应的标准名称；
        如果不在词表中，返回 None。

        Args:
            school: 流派名称
            field_path: 字段路径
            term: 待归一化的术语

        Returns:
            标准名称，或 None (如果不在词表中)
        """
        valid_values = set(self.get_valid_values(school, field_path))
        if term in valid_values:
            return term

        aliases_map = self.get_aliases_map(school, field_path)
        return aliases_map.get(term)

    def normalize_terms(
        self,
        school: SchoolType,
        field_path: str,
        terms: list[str],
    ) -> tuple[list[str], list[str]]:
        """批量归一化术语列表。

        Args:
            school: 流派名称
            field_path: 字段路径
            terms: 待归一化的术语列表

        Returns:
            (归一化后的术语列表, 无法识别的术语列表)
        """
        # 一次性构造查找结构，避免 O(N) 重复 get_valid_values + get_aliases_map
        valid_set = set(self.get_valid_values(school, field_path))
        aliases_map = self.get_aliases_map(school, field_path)
        normalized = []
        unknown = []

        for term in terms:
            if term in valid_set:
                normalized.append(term)
            elif term in aliases_map:
                normalized.append(aliases_map[term])
            else:
                unknown.append(term)

        return normalized, unknown

    def generate_llm_constraint(
        self,
        school: SchoolType,
        field_path: str,
        *,
        max_values: int | None = 50,
        include_english: bool = False,
    ) -> str:
        """生成 LLM Prompt 的 Enum 约束文本。

        Args:
            school: 流派名称
            field_path: 字段路径
            max_values: 最大显示值数量 (超过则截断并添加 "..." 提示)
            include_english: True 时每个选项格式为 "canonical (english)"，
                缺失 english 字段的条目回退到仅 canonical。

        Returns:
            约束文本，例如 "【stars】必须从以下选项中选择: 紫微, 天机, 太阳, ..."
            或（include_english=True）"【major_arcana】必须从以下选项中选择: 愚者 (The Fool), ..."

        Example:
            >>> registry.generate_llm_constraint("紫微", "palaces.twelve_palaces")
            '【twelve_palaces】必须从以下选项中选择: 命宫, 兄弟宫, 夫妻宫, ...'
        """
        values = self.get_valid_values(school, field_path)
        if not values:
            values = self.get_valid_values(school, field_path, key="name")

        # 提取字段显示名称 (最后一级)
        field_name = field_path.split(".")[-1]

        if not values:
            return f"【{field_name}】无可用选项"

        if include_english:
            # Build canonical → english lookup from raw entries
            vocab = self.load(school)
            raw_entries = _get_nested_value(vocab, field_path)
            en_lookup: dict[str, str] = {}
            _collect_english_values(raw_entries, en_lookup)
            display_values_list = [f"{c} ({en_lookup[c]})" if c in en_lookup else c for c in values]
        else:
            display_values_list = values

        if max_values is not None and len(display_values_list) > max_values:
            display_values = display_values_list[:max_values]
            suffix = f" ... (共 {len(display_values_list)} 项)"
        else:
            display_values = display_values_list
            suffix = ""

        return f"【{field_name}】必须从以下选项中选择: {', '.join(display_values)}{suffix}"

    def generate_llm_constraint_bilingual(
        self,
        school: SchoolType,
        field_path: str,
        *,
        max_values: int = 50,
    ) -> str:
        """generate_llm_constraint 的双语便捷方法 (include_english=True)。

        Args:
            school: 流派名称
            field_path: 字段路径
            max_values: 最大显示值数量

        Returns:
            双语约束文本，如 "【major_arcana】必须从以下选项中选择: 愚者 (The Fool), ..."
        """
        return self.generate_llm_constraint(school, field_path, max_values=max_values, include_english=True)

    def generate_all_constraints(
        self,
        school: SchoolType,
        field_paths: list[str],
        *,
        max_values: int | None = 30,
        include_english: bool = False,
    ) -> str:
        """生成多个字段的约束文本 (用于 LLM Prompt)。

        Args:
            school: 流派名称
            field_paths: 字段路径列表
            max_values: 每个字段最大显示值数量
            include_english: 透传至 generate_llm_constraint，True 时启用双语格式

        Returns:
            多行约束文本
        """
        constraints = []
        for path in field_paths:
            constraint = self.generate_llm_constraint(
                school, path, max_values=max_values, include_english=include_english
            )
            constraints.append(constraint)
        return "\n".join(constraints)

    def get_term_id_map(
        self,
        school: SchoolType,
        field_path: str,
    ) -> dict[str, str]:
        """获取 canonical → term_id 映射表。

        遍历 field_path 下的所有条目，提取 ``canonical`` → ``id`` 映射。

        **Fallback 策略**: 若某条目没有 ``id`` 字段，则以 ``canonical`` 本身作为
        term_id（透明 passthrough）。调用方若需区分"有真实 id"和"fallback canonical"，
        可与 ``get_valid_values()`` 交叉对比。

        Args:
            school: 流派名称
            field_path: 字段路径（如 ``"stars.main_stars"``）

        Returns:
            ``{canonical: term_id}`` 映射。若无 canonical 字段则跳过该条目。

        Example:
            >>> registry.get_term_id_map("紫微", "stars.main_stars")
            {'紫微': 'star_ziwei', '天机': 'star_tianji', ...}
        """
        vocab = self.load(school)
        entries = _get_nested_value(vocab, field_path)

        if entries is None:
            return {}

        id_map: dict[str, str] = {}
        missing_ids: list[str] = []

        def _process_entry(entry: Any) -> None:
            if not isinstance(entry, dict):
                return
            canonical = entry.get("canonical")
            if not canonical or not isinstance(canonical, str):
                return
            term_id = entry.get("id")
            if term_id and isinstance(term_id, str):
                id_map[canonical] = term_id
            else:
                # Fallback: 没有 id 字段，用 canonical 本身作为 term_id
                id_map[canonical] = canonical
                missing_ids.append(canonical)

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if isinstance(node.get("canonical"), str):
                    _process_entry(node)
                else:
                    for child in node.values():
                        walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)

        walk(entries)

        # 去重 + 过滤已 warned 的条目，剩余一次性 warn
        new_missing = [
            c
            for c in dict.fromkeys(missing_ids)  # 去重保序
            if (school, field_path, c) not in self._fallback_warned
        ]
        if new_missing:
            _log.warning(
                "vocab 条目缺少 id 字段, fallback canonical→canonical: school=%s field_path=%s canonicals=%s",
                school,
                field_path,
                new_missing,
            )
            for c in new_missing:
                self._fallback_warned.add((school, field_path, c))

        return id_map

    def get_field_metadata(
        self,
        school: SchoolType,
        field_path: str,
    ) -> dict[str, Any] | None:
        """获取字段的完整元数据。

        返回指定路径下的所有条目 (包含 id, canonical, aliases 等完整信息)。

        Args:
            school: 流派名称
            field_path: 字段路径

        Returns:
            字段条目列表或字典，或 None
        """
        vocab = self.load(school)
        return cast("dict[str, Any] | None", _get_nested_value(vocab, field_path))

    def list_fields(self, school: SchoolType) -> list[str]:
        """列出指定流派词表的顶层字段。

        Args:
            school: 流派名称

        Returns:
            顶层字段名列表 (排除 _meta)
        """
        vocab = self.load(school)
        return [k for k in vocab if not k.startswith("_")]

    def get_meta(self, school: SchoolType) -> dict[str, Any]:
        """获取词表的元信息。

        Args:
            school: 流派名称

        Returns:
            _meta 字段内容
        """
        vocab = self.load(school)
        return cast(dict[str, Any], vocab.get("_meta", {}))


# ═══════════════════════════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════════════════════════


@lru_cache(maxsize=1)
def get_registry() -> VocabularyRegistry:
    """获取全局单例 VocabularyRegistry。"""
    return VocabularyRegistry()


def get_valid_values(school: SchoolType, field_path: str) -> list[str]:
    """便捷函数：获取某字段的所有合法值。"""
    return get_registry().get_valid_values(school, field_path)


def normalize_term(school: SchoolType, field_path: str, term: str) -> str | None:
    """便捷函数：归一化术语。"""
    return get_registry().normalize_term(school, field_path, term)


def generate_constraint(school: SchoolType, field_path: str) -> str:
    """便捷函数：生成 LLM 约束。"""
    return get_registry().generate_llm_constraint(school, field_path)
