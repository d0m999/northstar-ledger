"""Vocab Migration Runtime — 加载 migrations/*.yaml 构造 alias map。

Batch B 实现范围:
- 加载 dictionaries/migrations/**/*.yaml 定义的迁移规则
- 支持 kind: rename（字段重命名 + 值重映射）
- 支持 kind: add_category / add_number_key / drop_value（仅记录，不实施 drop）
- 支持 kind: semantic_drift（Wave 3 接入 breaking:true 扫描 —
  汇总 compatibility.chunk_fields_affected 供 audit / reupsert 消费）
- resolve_field(school, field) — 返回字段的新名称（若无重命名则原样返回）
- resolve_value(school, field, value) — 返回值的 canonical 映射（若无则原样返回）
- get_breaking_migrations(school_code) — Wave 3 新增：按流派列出 breaking:true 迁移
- iter_breaking_field_paths(school_code) — Wave 3 新增：去重枚举 chunk_fields_affected
- term_id 映射留 Batch C
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent.parent / "dictionaries" / "migrations"

# 支持的 rule kinds（semantic_drift 自 Wave 3 起纳入，用于 breaking:true payload 扫描；
# schema_evolution 自 T4.E1 纳入，为 additive-only 字段增补，仅记录元数据）
_SUPPORTED_KINDS = frozenset(
    {
        "rename",
        "add_category",
        "add_number_key",
        "drop_value",
        "semantic_drift",
        "schema_evolution",
    }
)

# 流派中文名 → 英文 school_code（与 payload_projector._SCHOOL_EXT_KEY 对齐）
# audit / reupsert 消费侧拿到的是 payload 的 english school_code，而迁移 YAML
# 里 _meta.school 用的是中文全称；为了让 Wave 3 breaking 扫描 API 对消费端
# 零配置，本模块自己维护一份映射，避免跨包 import 拖环。
_SCHOOL_CODE_MAP: dict[str, str] = {
    "紫微": "ziwei",
    "六爻": "liuyao",
    "塔罗": "tarot",
    "西占": "western",
}


@dataclass(frozen=True)
class BreakingMigration:
    """一个 breaking:true 迁移的元数据快照。

    - ``school_cn`` / ``school_code``: 中文 / 英文两种命名都保留，方便不同消费端；
      英文 ``school_code`` 没有映射时为空字符串。
    - ``from_version`` / ``to_version``: ``_meta`` 声明的版本区间；用于判定
      某 payload 的 ``vocab_version`` 是否跨越了本次 breaking。
    - ``chunk_fields_affected``: ``compatibility.chunk_fields_affected`` 原样保留
      （含 school 前缀，如 ``tarot.elements.fire.imbalance``），供消费端做
      payload 路径残留扫描。
    - ``source_path``: 迁移 YAML 的绝对路径，便于错误提示定位。
    """

    school_cn: str
    school_code: str
    from_version: str
    to_version: str
    chunk_fields_affected: tuple[str, ...]
    source_path: Path


class VocabMigrator:
    """Vocab 迁移 runtime — 从 migrations/*.yaml 构造 field/value 映射表。

    Example:
        >>> migrator = VocabMigrator()
        >>> migrator.resolve_value("西占", "chart_type", "过境")
        '行运盘'
        >>> migrator.resolve_value("西占", "chart_type", "本命盘")
        '本命盘'
    """

    def __init__(self, migrations_dir: Path | None = None) -> None:
        self._dir = (migrations_dir or _MIGRATIONS_DIR).resolve()
        # school -> field -> new_field_name
        self._field_rename: dict[str, dict[str, str]] = {}
        # school -> field -> old_value -> new_value
        self._value_rename: dict[str, dict[str, dict[str, str]]] = {}
        # school -> field -> set[str] (dropped values)
        self._dropped_values: dict[str, dict[str, set[str]]] = {}
        # school_code (english) -> list[BreakingMigration]（Wave 3 breaking 扫描源）
        self._breaking: dict[str, list[BreakingMigration]] = {}
        # 已加载的流派集合
        self.schools: set[str] = set()
        self._load_all()

    def _load_all(self) -> None:
        """加载 migrations 目录下所有 v*.yaml 迁移文件。"""
        if not self._dir.is_dir():
            _log.warning("migrations 目录不存在，跳过加载: %s", self._dir)
            return

        for yaml_path in sorted(self._dir.rglob("v*.yaml")):
            self._load_file(yaml_path)

    def _load_file(self, yaml_path: Path) -> None:
        """加载单个迁移 YAML 文件。"""
        try:
            with open(yaml_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except Exception as e:
            _log.warning("跳过无法解析的迁移文件 %s: %s", yaml_path, e)
            return

        if not data or not isinstance(data, dict):
            return

        meta = data.get("_meta", {})
        school = meta.get("school", "")
        if not school:
            _log.warning("迁移文件缺少 _meta.school，跳过: %s", yaml_path)
            return

        self.schools.add(school)

        # Wave 3: 提前收集 _meta.breaking=true 的迁移（semantic_drift 或其他 kind
        # 都适用），下面的 rules 循环只负责已有 kind 的映射副作用。
        if meta.get("breaking") is True:
            from_ver = str(meta.get("from_version") or "").strip()
            to_ver = str(meta.get("to_version") or "").strip()
            compatibility = data.get("compatibility") or {}
            raw_paths = compatibility.get("chunk_fields_affected") or []
            paths: tuple[str, ...] = tuple(str(p).strip() for p in raw_paths if str(p).strip())
            if not from_ver or not to_ver:
                raise ValueError(f"breaking 迁移缺少 _meta.from_version 或 _meta.to_version，文件: {yaml_path}")
            if "chunk_fields_affected" not in compatibility:
                raise ValueError(f"breaking 迁移缺少 compatibility.chunk_fields_affected 字段，文件: {yaml_path}")
            if from_ver and to_ver and paths:
                entry = BreakingMigration(
                    school_cn=school,
                    school_code=_SCHOOL_CODE_MAP.get(school, ""),
                    from_version=from_ver,
                    to_version=to_ver,
                    chunk_fields_affected=paths,
                    source_path=yaml_path,
                )
                key = entry.school_code or school
                self._breaking.setdefault(key, []).append(entry)

        for rule in data.get("rules", []):
            if not isinstance(rule, dict):
                continue
            kind = rule.get("kind")
            if kind not in _SUPPORTED_KINDS:
                _log.warning("不支持的 kind=%r（Batch B 范围外），跳过规则: %s", kind, yaml_path)
                continue

            if kind == "rename":
                field = rule.get("field", "")
                to = rule.get("to")
                value_map: dict[str, str] = rule.get("value_map") or {}

                if to:
                    self._field_rename.setdefault(school, {})[field] = to

                if value_map:
                    self._value_rename.setdefault(school, {}).setdefault(field, {}).update(value_map)

            elif kind == "drop_value":
                field = rule.get("field", "")
                values: list[str] = rule.get("values") or []
                if field and values:
                    self._dropped_values.setdefault(school, {}).setdefault(field, set()).update(values)

            # add_category / add_number_key / semantic_drift / schema_evolution
            # — 仅记录元数据，实际消费走 _meta.breaking=true 路径（Wave 3）

    def resolve_term_candidates(self, school: str, term_id: str, *, canonical: str | None = None) -> tuple[str, ...]:
        """Return all ambiguous legacy candidates; source canonical may disambiguate."""
        from .vocab_loader import get_registry

        registry = get_registry()
        vocab = registry.load(school)
        candidates = tuple(vocab.get("legacy_ambiguous_ids", {}).get(term_id, [term_id]))
        if canonical is not None:
            match = registry.get_term_id_map(school, "patterns").get(canonical)
            if match is not None and match in candidates:
                return (match,)
        return candidates

    def resolve_field(self, school: str, field: str) -> str:
        """返回字段的新名称（若无重命名规则则原样返回）。

        Args:
            school: 流派名称
            field: 原字段名

        Returns:
            新字段名，或原字段名（若无重命名）
        """
        return self._field_rename.get(school, {}).get(field, field)

    def resolve_value(self, school: str, field: str, value: str) -> str | None:
        """返回值的 canonical 映射（若无则原样返回；若已 drop 则返回 None）。

        四象限决策表：

        +--------------------+----------------------------+------------------+
        | drop_value 命中?   | value_map 命中?            | 返回值           |
        +--------------------+----------------------------+------------------+
        | (a) Yes            | 不适用                     | None             |
        | (b) No             | Yes                        | 映射后的新值     |
        | (c) No             | No                         | 原值（原样返回） |
        | (d) school/field   | 未知（无任何规则）         | 原值（原样返回） |
        |     未知           |                            |                  |
        +--------------------+----------------------------+------------------+

        **重要约束**：调用方必须先判 ``is None`` 再判 equality，
        不能用 ``resolve_value(...) or value`` 兜底——因为合法的
        空字符串映射值 ``""`` 会被 ``or`` 误判为缺失。

        Args:
            school: 流派名称（如 ``"西占"``、``"紫微"``）
            field: 字段名（如 ``"chart_type"``）
            value: 原值

        Returns:
            - ``None``：值已被 drop_value 规则废弃（象限 a）
            - 映射后的 canonical 值（象限 b）
            - 原值（象限 c / d，无任何规则命中）
        """
        # 检查是否已 drop
        if school in self._dropped_values:
            dropped_for_field = self._dropped_values[school].get(field, set())
            if value in dropped_for_field:
                return None

        # 查找 value_map
        return self._value_rename.get(school, {}).get(field, {}).get(value, value)

    # ------------------------------------------------------------------
    # Wave 3 — breaking migration 扫描 API
    # ------------------------------------------------------------------

    def get_breaking_migrations(self, school_code: str | None = None) -> list[BreakingMigration]:
        """返回 breaking:true 迁移列表。

        Args:
            school_code: 英文流派代码（如 ``"tarot"`` / ``"western"``）。
                未提供时返回全部流派聚合结果。中文 _meta.school 无映射时，
                调用方也可以用中文全称作为 key 查询；默认按英文代码查。

        Returns:
            按 YAML 加载顺序排序的迁移列表（副本，调用方可自由修改）。
        """
        if school_code is None:
            result: list[BreakingMigration] = []
            for entries in self._breaking.values():
                result.extend(entries)
            return result
        return list(self._breaking.get(school_code, ()))

    def iter_breaking_field_paths(self, school_code: str) -> list[str]:
        """去重列出该流派所有 breaking 迁移的 ``chunk_fields_affected`` 路径。

        路径保留原 YAML 声明的 school 前缀（如 ``tarot.elements.fire.imbalance``），
        由消费端决定是否剥离前缀做 payload 路径导航。
        """
        seen: set[str] = set()
        ordered: list[str] = []
        for entry in self._breaking.get(school_code, ()):
            for path in entry.chunk_fields_affected:
                if path not in seen:
                    seen.add(path)
                    ordered.append(path)
        return ordered


@lru_cache(maxsize=1)
def get_migrator() -> VocabMigrator:
    """获取全局单例 VocabMigrator。"""
    return VocabMigrator()
