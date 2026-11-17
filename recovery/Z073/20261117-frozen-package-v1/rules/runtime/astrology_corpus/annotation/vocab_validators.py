"""Pydantic field validator 工厂 — T1.1。

提供 make_membership_validator(category, school) 工厂函数，
返回一个 Pydantic v2 field_validator 兼容的 callable，
供 schema 字段挂接受控词表校验。

注意（Pydantic v2 兼容性契约）：
调用方 MUST 使用 mode="before" 显式形式，随后由 Pydantic 校验类型和空值::

    field_validator("field_name", mode="before")(make_membership_validator(...))

不要依赖 Pydantic 对 bare callable 的 signature 推断 — 未文档化、升级可能静默失效。

使用示例::

    from pydantic import BaseModel, field_validator
    from astrology_corpus.annotation.vocab_validators import make_membership_validator

    class MyModel(BaseModel):
        palace: str | None
        _validate_palace = field_validator("palace", mode="before")(
            make_membership_validator("palaces.twelve_palaces", "紫微")
        )
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from .vocab_loader import SchoolType, get_registry

_VALID_VALUE_TYPES: Final[frozenset[str]] = frozenset({"str", "int"})
# 与 VocabularyRegistry.get_valid_values 的 value_shape 分派保持一致。
# 新增 shape 时须同时更新 vocab_loader.py::get_valid_values 的分支。
_VALID_VALUE_SHAPES: Final[frozenset[str]] = frozenset({"list", "dict_of_dicts", "dict_of_lists"})


def make_membership_validator(
    category: str,
    school: SchoolType,
    *,
    value_shape: str = "list",
    value_type: str = "str",
    expect_scalar: bool = False,
) -> Callable[[Any], Any]:
    """创建词表成员校验器。

    Args:
        category: 词表字段路径，如 "palaces.twelve_palaces"。
        school:   流派名称，必填，不支持 None（跨流派并集留待后续 Batch）。
        value_shape: 词表结构类型，透传给 get_valid_values。
            "list" (默认) — 走标准列表结构；
            "dict_of_dicts" — dict[str, dict] 结构，提取 canonical 字段。
        value_type: 值类型。
            "str" (默认) — 构造 frozenset[str]；
            "int" — 从 get_int_values() 构造 frozenset[int]，validate int 成员。
        expect_scalar: True 时拒绝 list/tuple/set 输入，仅接受单值或 None。
            用于 str | None / int | None 标量字段（如 knowledge_domain）。
            False（默认）时接受 list 输入，用于 list[str] / list[int] 字段。
            字段本身的 list/scalar 与 nullable 约束由后续 Pydantic 类型校验负责。

    Returns:
        一个 callable，可直接作为 Pydantic v2 ``field_validator`` 的装饰目标。

    Raises:
        ValueError: school 为 None 时；或 category 在词表中不存在（空集）时。

    Pydantic 版本契约（pydantic >= 2.0, < 3.0）：
        调用方 MUST 使用 mode="before" 显式形式::

            field_validator("field_name", mode="before")(make_membership_validator(...))

        不要依赖 Pydantic 对 bare callable 的 signature 推断 — 未文档化、升级可能静默失效。
        before 接收原始值，之后仍执行字段类型校验，保留 JSON Schema 类型信息。

    frozenset 快照语义：
        ``valid = frozenset(...)`` 在工厂调用时构造一次，与进程生命周期绑定（见 docstring "frozenset 快照语义"）。
        生产环境 VOCAB_HOT_RELOAD=0（默认）下词表不会热更新。
        测试环境 reload 后需重建 validator（重新调用本工厂）。

    参数顺序警告：
        ``make_membership_validator(category, school)`` 与
        ``get_valid_values(school, category)`` 参数顺序**相反**，请勿混淆。

    Example::

        class Demo(BaseModel):
            palace: str | None
            _v = field_validator("palace", mode="before")(
                make_membership_validator("palaces.twelve_palaces", "紫微")
            )
    """
    if school is None:
        raise ValueError("school 不能为 None — Batch A 不支持跨流派并集")

    if value_type not in _VALID_VALUE_TYPES:
        raise ValueError(f"未知 value_type: {value_type!r}，合法值: {sorted(_VALID_VALUE_TYPES)}")
    if value_shape not in _VALID_VALUE_SHAPES:
        raise ValueError(f"未知 value_shape: {value_shape!r}，合法值: {sorted(_VALID_VALUE_SHAPES)}")

    registry = get_registry()

    if value_type == "int":
        # frozenset[int] — 用于 houses_mentioned 等整数列表字段
        valid_ints: frozenset[int] = frozenset(registry.get_int_values(school, category))

        if not valid_ints:
            raise ValueError(f"未知词表 category (int): {school}/{category}")

        def _validate_int(value: Any) -> Any:
            if value is None:
                return value
            if expect_scalar and isinstance(value, (list, tuple, set)):
                raise ValueError(f"字段 {school}/{category} 要求标量 int，收到序列 {type(value).__name__}")
            items = value if isinstance(value, list) else [value]
            for v in items:
                if type(v) is not int:
                    raise ValueError(f"字段 {school}/{category} 要求 int，收到 {type(v).__name__}")
                if v not in valid_ints:
                    raise ValueError(f"值「{v}」不在词表 {school}/{category} 的合法 int 值中")
            return value

        return _validate_int

    # frozenset 快照：构造时一次性读取，与进程生命周期绑定（见 docstring "frozenset 快照语义"）
    valid: frozenset[str] = frozenset(registry.get_valid_values(school, category, value_shape=value_shape)) | frozenset(
        registry.get_zh_aliases_map(school, category).keys()
    )

    if not valid:
        raise ValueError(f"未知词表 category: {school}/{category}")

    def _validate(value: Any) -> Any:
        if value is None:
            return value
        if expect_scalar and isinstance(value, (list, tuple, set)):
            raise ValueError(f"字段 {school}/{category} 要求标量 str，收到序列 {type(value).__name__}")
        items = value if isinstance(value, list) else [value]
        for v in items:
            if not isinstance(v, str):
                raise ValueError(f"字段 {school}/{category} 要求 str，收到 {type(v).__name__}")
            if v not in valid:
                raise ValueError(f"值「{v}」不在词表 {school}/{category} 中")
        return value

    return _validate
