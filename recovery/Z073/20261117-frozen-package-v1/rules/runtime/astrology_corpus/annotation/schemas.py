"""语料标注 Pydantic Schema 定义。

Simplified (MVP) 与 Full (生产) 两层 Schema 设计：
- ChunkMetadata: 9 字段简化版，用于 MVP 标注
- ChunkMetadataFull: 继承 Simplified，加入 QA 字段

§1.3.6 宫位图多模态处理 Schema：
- ChartType: 命盘图类型枚举
- PalaceInfo: 宫位信息
- DetectedImage: TextIn 检测到的图像元素
- ImageChunk: 图像块记录 (含 vision_summary)
"""

# SYNC: 修改本文件的 Extension 字段后需同步以下文件:
# - .claude/commands/annotate-chunks.md  §3.1 + §3.6
# - .claude/commands/audit-annotations.md  §审核范围
# - llm_annotator.py  OUTPUT_SCHEMAS
# - reannotator.py  TERM_FIELDS (if list field added)

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from ._cjk import contains_cjk
from .semantic_models import (
    FieldEvidence,
    LayoutMention,
    RetrievalEligibility,
    SemanticExtension,
    SpiritRoleMention,
    SpreadMention,
    TimeContextMention,
)
from .vocab_validators import make_membership_validator

# ═══════════════════════════════════════════════════════════════
# 常量: 预定义标签
# ═══════════════════════════════════════════════════════════════

MAIN_STARS: list[str] = [
    "紫微",
    "天机",
    "太阳",
    "武曲",
    "天同",
    "廉贞",
    "天府",
    "太阴",
    "贪狼",
    "巨门",
    "天相",
    "天梁",
    "七杀",
    "破军",
]

LUCKY_STARS: list[str] = ["左辅", "右弼", "文昌", "文曲", "天魁", "天钺"]

UNLUCKY_STARS: list[str] = ["火星", "铃星", "擎羊", "陀罗", "地空", "地劫"]

ALL_STARS: list[str] = MAIN_STARS + LUCKY_STARS + UNLUCKY_STARS

PALACES: list[str] = [
    "命宫",
    "兄弟宫",
    "夫妻宫",
    "子女宫",
    "财帛宫",
    "疾厄宫",
    "迁移宫",
    "交友宫",
    "官禄宫",
    "田宅宫",
    "福德宫",
    "父母宫",
]

DIFFICULTY_LEVELS: list[str] = ["easy", "medium", "hard"]

# school 中文标签 → 机器可读 school_code 映射 (single source-of-truth)
# 与 SCHOOL_EXTENSION_MAP (annotate_reviewer.py) 不同：本映射对 "通用" 返回 "generic"，
# 而 SCHOOL_EXTENSION_MAP 对 "通用" 返回 None（用于扩展字段选择）。
SCHOOL_TO_CODE: dict[str, str] = {
    "紫微": "ziwei",
    "六爻": "liuyao",
    "西占": "western",
    "塔罗": "tarot",
    "通用": "generic",
}


# ═══════════════════════════════════════════════════════════════
# Stage 1 输出: 预处理后的 Chunk 记录
# ═══════════════════════════════════════════════════════════════


class ChunkRecord(BaseModel):
    """preprocess.py 输出的 JSONL 记录 — 纯文本 chunk + 溯源信息。"""

    chunk_id: str = Field(
        ...,
        pattern=r"^[a-z0-9_]+_c\d{3,}$",
        description="唯一标识符，格式: {source_stem}_c{index:03d}",
    )
    source_file: str = Field(
        ...,
        min_length=1,
        description="来源 PDF 文件名",
    )
    page_range: list[int] = Field(
        ...,
        min_length=1,
        description="来源页码 (1-based)",
    )
    chunk_text: str = Field(
        ...,
        min_length=1,
        description="块文本内容",
    )
    chunk_index: int = Field(
        ...,
        ge=1,
        description="在源文件中的块序号 (1-based)",
    )


# ═══════════════════════════════════════════════════════════════
# Stage 3 输出: 标注 Metadata (Simplified — 9 字段)
# ═══════════════════════════════════════════════════════════════


class ChunkMetadata(BaseModel):
    """MVP 标注输出的 metadata 子集 (12 字段)。"""

    chunk_id: str = Field(..., pattern=r"^[a-z0-9_]+_c\d{3,}$")
    source_file: str = Field(..., min_length=1)
    page_range: list[int] = Field(..., min_length=1)
    school: Literal["紫微", "六爻", "西占", "塔罗", "通用"] = Field(..., description="命理流派")
    content_type: Literal["概念解释", "实战断语", "案例分析", "古籍原文", "问答对话"] = Field(
        ..., description="内容类型"
    )
    main_stars: list[str] = Field(default_factory=list, description="涉及的星曜")
    main_palaces: list[str] = Field(default_factory=list, description="涉及的宫位")
    difficulty: Literal["easy", "medium", "hard"] = Field(..., description="难度评估")
    summary: str = Field(..., max_length=300, description="内容摘要 (≤300 字)")
    keywords: list[str] = Field(default_factory=list, max_length=10, description="关键词 (≤10 个)")
    content_language: Literal["zh", "en"] = Field(
        default="zh",
        description="语料语言 (zh=中文, en=英文)",
    )
    school_code: Literal["ziwei", "liuyao", "western", "tarot", "generic"] = Field(
        default="generic",
        description="机器可读的流派代码，用于向量库 metadata filter",
    )


# ═══════════════════════════════════════════════════════════════
# Stage 3 输出: 完整标注记录 (chunk_text + metadata)
# ═══════════════════════════════════════════════════════════════


class AnnotatedChunk(BaseModel):
    """带 metadata 的完整标注记录 — JSONL 交换格式。"""

    chunk_id: str = Field(..., pattern=r"^[a-z0-9_]+_c\d{3,}$")
    source_file: str = Field(..., min_length=1)
    page_range: list[int] = Field(..., min_length=1)
    chunk_text: str = Field(..., min_length=1)
    chunk_index: int = Field(..., ge=1)

    # Metadata 字段 (from ChunkMetadata)
    school: Literal["紫微", "六爻", "西占", "塔罗", "通用"]
    content_type: Literal["概念解释", "实战断语", "案例分析", "古籍原文", "问答对话"]
    main_stars: list[str] = Field(default_factory=list)
    main_palaces: list[str] = Field(default_factory=list)
    difficulty: Literal["easy", "medium", "hard"]
    summary: str = Field(..., max_length=300)
    keywords: list[str] = Field(default_factory=list, max_length=10)

    annotation_evidence: list[FieldEvidence] = Field(default_factory=list)

    # PR-3: 双语 + 流派代码 (与 ChunkMetadata 同步)
    content_language: Literal["zh", "en"] = Field(
        default="zh",
        description="语料语言 (zh=中文, en=英文)",
    )
    school_code: Literal["ziwei", "liuyao", "western", "tarot", "generic"] = Field(
        default="generic",
        description="机器可读的流派代码，用于向量库 metadata filter",
    )

    # Phase 1 增强: 双向关联绑定 — 从文本 chunk 引用关联的图像 chunk
    associated_image_chunk_ids: list[str] = Field(
        default_factory=list,
        description="关联的图像 chunk ID (同页/相邻页的命盘图)",
    )


# ═══════════════════════════════════════════════════════════════
# Full Schema (生产环境): 继承 Simplified + QA 字段
# ═══════════════════════════════════════════════════════════════


class ChunkMetadataFull(AnnotatedChunk):
    """完整 metadata — 含 QA 审核字段，对齐 dev-annotation-guidelines.md。"""

    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0, description="LLM 标注置信度")
    needs_review: bool = Field(default=False, description="是否需要人工复核")
    review_status: Literal["pending", "approved", "rejected", "revised"] = Field(
        default="pending", description="审核状态"
    )
    annotator: str | None = Field(default=None, description="标注人员 ID")
    reviewer: str | None = Field(default=None, description="审核人员 ID")
    annotation_timestamp: datetime | None = Field(default=None, description="标注时间")
    review_timestamp: datetime | None = Field(default=None, description="审核时间")
    revision_notes: str | None = Field(default=None, description="修订说明")


# ═══════════════════════════════════════════════════════════════
# Stage 4 输出: 校验报告
# ═══════════════════════════════════════════════════════════════


class ValidationIssue(BaseModel):
    """单条校验问题。"""

    chunk_id: str
    field: str
    issue: str
    severity: Literal["error", "warning"]


class ValidationReport(BaseModel):
    """Stage 4 校验报告。"""

    total_records: int = Field(..., ge=0)
    valid_count: int = Field(..., ge=0)
    invalid_count: int = Field(..., ge=0)
    review_sample_count: int = Field(..., ge=0, description="抽检数量")
    pass_rate: float = Field(..., ge=0.0, le=1.0)
    issues: list[ValidationIssue] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# §1.3.6 宫位图多模态处理 Schema
# ═══════════════════════════════════════════════════════════════


class ChartType(str, Enum):
    """命盘图类型。"""

    NATAL_CHART = "natal_chart"
    FLYING_STAR = "flying_star"
    PALACE_LAYOUT = "palace_layout"
    STAR_DIAGRAM = "star_diagram"
    UNKNOWN = "unknown"


class PalaceInfo(BaseModel):
    """宫位信息 — 从人工审核 Vision JSON 提取的单个宫位描述。"""

    palace_name: str = Field(..., description="宫位名称 (如 命宫、财帛宫)")
    main_stars: list[str] = Field(default_factory=list, description="宫内主星")
    minor_stars: list[str] = Field(default_factory=list, description="宫内辅星/煞星")
    position: str | None = Field(default=None, description="宫位方位 (如 子、丑)")


class DetectedImage(BaseModel):
    """TextIn 版面分析检测到的图像元素 (domain post-filter 前)。

    来源: TextIn xParse API `get_image="objects"` 返回的图像元素。
    """

    page_id: int = Field(..., description="所在页码 (0-based)")
    element_id: int = Field(default=0, description="TextIn 元素 ID")
    pos: list[int] = Field(..., description="四边形坐标 [x1,y1, x2,y2, x3,y3, x4,y4]")
    width: int = Field(..., ge=0, description="图像宽度 (像素)")
    height: int = Field(..., ge=0, description="图像高度 (像素)")
    score: float = Field(..., ge=0.0, le=1.0, description="TextIn 检测置信度")
    image_data: bytes | None = Field(default=None, exclude=True, description="图像二进制数据")
    image_uri: str | None = Field(default=None, description="保存后的图像路径/URI")

    @property
    def aspect_ratio(self) -> float:
        """宽高比 (width / height)。"""
        return self.width / self.height if self.height > 0 else 0.0

    @property
    def area(self) -> int:
        """图像面积 (像素)。"""
        return self.width * self.height

    def area_ratio(self, page_width: int, page_height: int) -> float:
        """图像面积占页面面积的比例。"""
        page_area = page_width * page_height
        return self.area / page_area if page_area > 0 else 0.0


class ImageChunk(BaseModel):
    """图像块记录 — 经过 domain filter 确认的命盘图 + vision_summary。

    用于 Qdrant 向量化: vision_summary 文本嵌入为向量，
    存入与文本 chunk 相同的集合，通过 chunk_type="image" 区分。
    """

    chunk_id: str = Field(
        ...,
        pattern=r"^[a-z0-9_]+_img\d{3,}$",
        description="唯一标识符，格式: {source_stem}_img{index:03d}",
    )
    chunk_type: Literal["image"] = Field(default="image", description="块类型")
    source_file: str = Field(..., min_length=1, description="来源 PDF 文件名")
    page_id: int = Field(..., ge=0, description="来源页码 (0-based)")
    image_type: ChartType = Field(default=ChartType.UNKNOWN, description="命盘图类型")
    image_uri: str = Field(..., description="图像文件路径 (02_extracted_assets/charts/)")
    vision_summary: str = Field(
        ...,
        min_length=1,
        description="人工审核 Vision JSON 生成的结构化描述文本",
    )
    palace_layout: list[PalaceInfo] = Field(default_factory=list, description="识别到的宫位布局")
    main_stars_detected: list[str] = Field(default_factory=list, description="识别到的星曜")
    confidence: float = Field(..., ge=0.0, le=1.0, description="TextIn 检测置信度")
    associated_text_chunk_ids: list[str] = Field(
        default_factory=list,
        description="关联的文本 chunk ID (同页/相邻页)",
    )

    # --- Phase 1 增强: 结构化元数据 (用于向量库 metadata filter) ---
    palaces_with_stars: list[str] = Field(
        default_factory=list,
        description="宫位_星曜 组合索引 (如 ['命宫_紫微', '命宫_天府', '财帛宫_武曲'])",
    )
    school: Literal["紫微", "六爻", "西占", "塔罗", "通用"] = Field(..., description="命理流派")
    content_language: Literal["zh", "en"] = Field(default="zh", description="语料语言")
    school_code: Literal["ziwei", "liuyao", "western", "tarot", "generic"] = Field(
        default="generic", description="机器可读的流派代码"
    )

    @model_validator(mode="after")
    def _derive_school_code(self) -> ImageChunk:
        """school_code 未显式指定时，从 school 自动派生。"""
        if self.school_code == "generic" and self.school != "通用":
            object.__setattr__(self, "school_code", SCHOOL_TO_CODE[self.school])
        return self

    has_sihua: bool = Field(default=False, description="是否包含四化标记 (化禄/化权/化科/化忌)")
    star_count: int = Field(default=0, ge=0, description="识别到的星曜数量 (判断命盘完整性)")

    def build_palaces_with_stars(self) -> list[str]:
        """从 palace_layout 生成宫位_星曜组合索引。"""
        combos: list[str] = []
        for p in self.palace_layout:
            for star in p.main_stars:
                combos.append(f"{p.palace_name}_{star}")
        return combos


# ═══════════════════════════════════════════════════════════════
# 领域扩展 Schema (Domain Extensions)
# 用于受控词表系统的流派特定字段
# ═══════════════════════════════════════════════════════════════

# --- 共享类型定义 ---
# T9: SchoolType 改为 str 类型别名，不再使用 typing.Literal，遵守 Vocab SoT Contract。
# 合法流派值由 SCHOOL_FILE_MAP（vocab_loader.py）动态提供，运行时由 make_membership_validator 校验。
SchoolType = str  # type alias；语义上等价 Literal["紫微", "六爻", "西占", "塔罗", "通用"]
# Batch B T1.3-c: 改为 str 类型别名，合法值由 time_contexts vocab 动态提供
# 旧: Literal["本命", "大限", "流年", "流月", "流日", "流时", "通用"]
TimeContextType = str  # type alias；语义合法值集合由 vocab_validators 动态校验


# ═══════════════════════════════════════════════════════════════
# 紫微斗数扩展 (ZW-01 ~ ZW-08)
# ═══════════════════════════════════════════════════════════════


class SihuaMention(BaseModel):
    """四化提及 — 标注文本中出现的四化信息。"""

    star: str | None = Field(default=None, description="化曜星曜；原文未指明时为null，不以四化名代替")
    # Batch B T1.3-c: 迁移至 vocab runtime 校验
    transformation: str = Field(..., description="四化类型")
    tiangan: str | None = Field(default=None, description="触发天干")
    palace: str | None = Field(default=None, description="承载星曜/四化的宫位，不是仅受影响的宫位")
    source_palace: str | None = Field(default=None, description="原文明确的飞化发出宫位")
    target_palace: str | None = Field(default=None, description="原文明确的飞入宫位，不是泛指影响对象")
    scope_id: str | None = None

    _validate_transformation = field_validator("transformation", mode="before")(
        make_membership_validator("transformations", "紫微", expect_scalar=True)
    )


class PatternMention(BaseModel):
    """格局提及 — 标注文本中出现的格局信息。"""

    pattern_name: str = Field(..., description="格局名称")
    # Batch B T1.3-c: 迁移至 vocab runtime 校验
    formation_status: str | None = Field(default=None, description="成格状态")
    evidence: str | None = Field(default=None, description="格局成立依据")

    _validate_formation_status = field_validator("formation_status", mode="before")(
        make_membership_validator("formation_status", "紫微", expect_scalar=True)
    )


class ZiweiExtension(SemanticExtension):
    """紫微斗数领域扩展字段 (ZW-01 ~ ZW-08)。

    用于 LLM 标注后的结构化输出，与 YAML 词表配合使用。
    """

    chart_type: str | None = None
    chart_types_mentioned: list[str] = Field(default_factory=list)
    relationship_types_mentioned: list[str] = Field(default_factory=list)
    comparison_methods: list[str] = Field(default_factory=list)
    time_mentions: list[TimeContextMention] = Field(default_factory=list)
    star_groups_mentioned: list[str] = Field(default_factory=list)

    knowledge_domain: str | None = Field(
        default=None,
        description="知识域分类；canonical 见 ziwei_vocabulary.yaml knowledge_domains section",
    )

    _validate_knowledge_domain = field_validator("knowledge_domain", mode="before")(
        make_membership_validator("knowledge_domains", "紫微", expect_scalar=True)
    )

    # ZW-01: 星曜
    stars_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的星曜 (标准名称)",
    )

    # ZW-02: 宫位
    palaces_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的宫位 (标准名称)",
    )

    # ZW-03: 四化
    sihua_mentioned: list[SihuaMention] = Field(
        default_factory=list,
        description="提及的四化",
    )

    # ZW-04: 格局
    patterns_mentioned: list[PatternMention] = Field(
        default_factory=list,
        description="提及的格局",
    )

    # ZW-05: 对星组合
    star_pairs_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的对星组合 (如 左辅右弼)",
    )

    # ZW-06: 流派变体 (Batch B T1.3-c: 迁移至 vocab runtime 校验)
    school_variant: str | None = Field(
        default=None,
        description="紫微流派变体",
    )

    # ZW-07: 主题关联
    topics_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的主题 (如 事业运势、财运分析)",
    )

    # ZW-08: 时间语境
    time_context: TimeContextType | None = Field(
        default=None,
        description="时间语境 (本命/大限/流年等)",
    )

    # ZW-09: 星曜交互 (同宫组合/夹辅/对宫)
    star_interactions_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的星曜交互 (如 紫府同临、桃花犯主)",
    )

    # Batch B T1.3-c: vocab runtime 校验
    _validate_school_variant = field_validator("school_variant", mode="before")(
        make_membership_validator("school_variants", "紫微", expect_scalar=True)
    )
    _validate_time_context = field_validator("time_context", mode="before")(
        make_membership_validator("time_contexts", "紫微", expect_scalar=True)
    )


# ═══════════════════════════════════════════════════════════════
# 六爻扩展 (LY-01 ~ LY-07)
# ═══════════════════════════════════════════════════════════════


class HexagramMention(BaseModel):
    """卦象提及 — 标注文本中出现的卦象信息。"""

    hexagram_name: str = Field(..., description="卦名 (如 乾为天)")
    palace: str | None = Field(default=None, description="原文明示的归属宫")
    derived_palace: str | None = Field(default=None, description="版本化词表查表派生，与原文陈述分开")
    scope_id: str | None = None
    is_main_hexagram: bool | None = Field(default=None, description="是否为主卦")
    is_changed_hexagram: bool | None = Field(default=None, description="是否为变卦")


class YaoMention(BaseModel):
    """爻位提及 — 标注文本中出现的爻位信息。"""

    position: str | None = Field(default=None, description="原文爻位表达；不推断未说明的阴阳")
    ordinal: int | None = Field(default=None, ge=1, le=6)
    polarity: str | None = None
    scope_id: str | None = None
    is_moving: bool | None = Field(default=None, description="是否为动爻")
    six_relative: str | None = Field(default=None, description="六亲 (父母/兄弟/...)")
    six_spirit: str | None = Field(default=None, description="六神 (青龙/朱雀/...)")


class LiuyaoExtension(SemanticExtension):
    """六爻领域扩展字段 (LY-01 ~ LY-07)。"""

    spirit_roles: list[SpiritRoleMention] = Field(default_factory=list)

    knowledge_domain: str | None = Field(
        default=None,
        description="知识域分类；canonical 见 liuyao_vocabulary.yaml knowledge_domains section",
    )

    _validate_knowledge_domain = field_validator("knowledge_domain", mode="before")(
        make_membership_validator("knowledge_domains", "六爻", expect_scalar=True)
    )

    # LY-01: 卦象
    hexagrams_mentioned: list[HexagramMention] = Field(
        default_factory=list,
        description="提及的卦象",
    )

    # LY-02: 爻位
    yao_mentioned: list[YaoMention] = Field(
        default_factory=list,
        description="提及的爻位",
    )

    # LY-03: 六亲
    six_relatives_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的六亲 (父母/兄弟/子孙/妻财/官鬼)",
    )

    # 六神
    six_spirits_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的六神 (青龙/朱雀/勾陈/螣蛇/白虎/玄武)",
    )

    # LY-04: 用神
    yong_shen: str | None = Field(
        default=None,
        description="用神类型",
    )

    # LY-05: 起卦方法
    divination_method: str | None = Field(
        default=None,
        description="起卦方法 (金钱卦/时间起卦/蓍草起卦等)",
    )

    # 问事类型
    question_type: str | None = Field(
        default=None,
        description="问事类型 (求财/问事业/问婚姻等)",
    )

    # LY-06: 八卦
    trigrams_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的八卦 (乾/坤/震/巽/坎/离/艮/兑)",
    )

    # LY-07: 五行
    five_phases_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的五行 (金/木/水/火/土)",
    )

    # LY-08: 爻变状态
    line_statuses_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的爻变状态 (老阴/少阳/少阴/老阳)",
    )

    # LY-09: 世应
    hexagram_roles_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的世应 (世/应)",
    )


# ═══════════════════════════════════════════════════════════════
# 塔罗扩展 (TR-01 ~ TR-06)
# ═══════════════════════════════════════════════════════════════


class CardMention(BaseModel):
    """牌面提及 — 标注文本中出现的塔罗牌信息。"""

    card_name: str = Field(..., description="牌名 (如 愚者、权杖三)")
    # Batch B T1.3-c: 迁移至 vocab runtime 校验
    position: str | None = Field(default=None, description="正逆位")
    spread_position: str | None = Field(default=None, description="牌阵位置 (如 过去、现在、未来)")

    _validate_position = field_validator("position", mode="before")(
        make_membership_validator("positions", "塔罗", expect_scalar=True)
    )


class TarotExtension(SemanticExtension):
    """塔罗领域扩展字段 (TR-01 ~ TR-06)。"""

    # Plan A (v1.7.0): 从 Literal 迁至 vocab runtime 校验，canonical 集从 vocab
    # YAML knowledge_domains 动态读取 (8 项: 牌义解读 / 牌阵应用 / 元素象征 /
    # 逆位理论 / 解读技法 / 占星对应 / 卡巴拉对应 / 数字象征)。
    spreads_used: list[str] = Field(default_factory=list)
    spread_mentions: list[SpreadMention] = Field(default_factory=list)
    layouts: list[LayoutMention] = Field(default_factory=list)

    knowledge_domain: str | None = Field(default=None, description="塔罗知识域")

    _validate_knowledge_domain = field_validator("knowledge_domain", mode="before")(
        make_membership_validator("knowledge_domains", "塔罗", expect_scalar=True)
    )

    # TR-01 & TR-02: 牌面
    cards_mentioned: list[CardMention] = Field(
        default_factory=list,
        description="提及的塔罗牌",
    )

    # TR-04: 牌阵
    spread_used: str | None = Field(
        default=None,
        description="使用的牌阵 (凯尔特十字/三张牌阵等)",
    )

    # TR-06: 元素 (Batch B T1.3-c: 迁移至 vocab runtime 校验, dict_of_dicts 结构)
    elements_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的元素",
    )

    # 花色 (Batch B T1.3-c: 迁移至 vocab runtime 校验)
    suits_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的花色",
    )

    # 问事主题
    reading_topic: str | None = Field(
        default=None,
        description="占卜主题 (爱情/事业/财务等)",
    )

    # TR-07: 牌组组合
    card_combinations_mentioned: list[list[str]] = Field(
        default_factory=list,
        description="提及的牌组组合 (如 [['愚者','魔术师']])",
    )

    # TR-08: 解读技法
    reading_technique: str | None = Field(
        default=None,
        description="解读技法 (直觉读牌/传统读牌/元素分析/数字分析/故事叙述/心理分析)",
    )

    # D-5: 新增多值提及字段
    spreads_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的牌阵",
    )
    positions_mentioned: list[str] = Field(
        default_factory=list,
        description="文章级别 positions 汇总（vocab: positions 词表，如正位/逆位），与 CardMention.position（单卡正/逆位卡牌级字段）语义层级不同",
    )
    reading_techniques_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的解读技法",
    )

    # Batch B T1.3-c: vocab runtime 校验
    _validate_elements_mentioned = field_validator("elements_mentioned", mode="before")(
        make_membership_validator("elements", "塔罗", value_shape="dict_of_dicts")
    )
    _validate_suits_mentioned = field_validator("suits_mentioned", mode="before")(
        make_membership_validator("suits", "塔罗")
    )
    # D-5: 新增字段 vocab runtime 校验
    _validate_spreads_mentioned = field_validator("spreads_mentioned", mode="before")(
        make_membership_validator("spreads", "塔罗")
    )
    _validate_positions_mentioned = field_validator("positions_mentioned", mode="before")(
        make_membership_validator("positions", "塔罗")
    )
    _validate_reading_techniques_mentioned = field_validator("reading_techniques_mentioned", mode="before")(
        make_membership_validator("reading_techniques", "塔罗")
    )


# ═══════════════════════════════════════════════════════════════
# 西方占星扩展 (WA-01 ~ WA-09)
# ═══════════════════════════════════════════════════════════════


class PlanetMention(BaseModel):
    """行星提及 — 标注文本中出现的行星信息。"""

    planet: str = Field(..., description="行星名称")
    scope_id: str | None = None
    sign: str | None = Field(default=None, description="所在星座")
    house: int | None = Field(default=None, ge=1, le=12, description="所在宫位")
    is_retrograde: bool | None = Field(default=None, description="是否逆行")


class AspectMention(BaseModel):
    """相位提及 — 标注文本中出现的相位信息。"""

    planet1: str = Field(..., description="行星1")
    planet2: str = Field(..., description="行星2")
    aspect_type: str = Field(..., description="相位类型 (合相/对冲/三合等)")


class WesternExtension(SemanticExtension):
    """西方占星领域扩展字段 (WA-01 ~ WA-09)。"""

    chart_types_mentioned: list[str] = Field(default_factory=list)

    # T1.8 + Batch B: 历史 JSONL 兼容层。
    #   1) 字段重命名: topics_mentioned → analysis_type_mentioned
    #   2) chart_type 值迁移: 过境→行运盘 / 推运→推运盘 / 太阳回归→太阳回归盘 / 卜卦→卜卦盘
    # 必须在 field_validator (mode="before") 之前执行，否则老 JSONL 会被 vocab 校验直接拒绝。
    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_fields(cls, data: Any, info: ValidationInfo) -> Any:
        if not isinstance(data, dict):
            return data
        migrated = data

        if (
            not (info.context or {}).get("new_semantics")
            and "topics_mentioned" in migrated
            and "analysis_type_mentioned" not in migrated
        ):
            legacy = migrated["topics_mentioned"]
            # 仅供历史数据读取：先迁移旧字段形状，再由 before validator
            # 与 Pydantic list[str] 类型共同校验。新写出口拒绝旧字段名。
            if isinstance(legacy, list):
                normalized: list[str] | None = legacy
            elif isinstance(legacy, str):
                normalized = [legacy]
            else:
                # None / dict / int / ... — 丢弃，让默认 [] 生效
                normalized = None
            migrated = {**migrated}
            migrated.pop("topics_mentioned", None)
            if normalized is not None:
                migrated["analysis_type_mentioned"] = normalized

        chart_type_val = migrated.get("chart_type")
        if isinstance(chart_type_val, str):
            # 延迟导入避免 schemas ↔ vocab_migrator 潜在循环
            from .vocab_migrator import get_migrator

            resolved = get_migrator().resolve_value("西占", "chart_type", chart_type_val)
            if resolved != chart_type_val:
                if migrated is data:
                    migrated = dict(data)
                migrated["chart_type"] = resolved  # None 表示该值已被 drop_value 废弃

        return migrated

    knowledge_domain: str | None = Field(
        default=None,
        description="知识域分类；canonical 见 western_vocabulary.yaml knowledge_domains section",
    )

    _validate_knowledge_domain = field_validator("knowledge_domain", mode="before")(
        make_membership_validator("knowledge_domains", "西占", expect_scalar=True)
    )

    # WA-01: 行星
    planets_mentioned: list[PlanetMention] = Field(
        default_factory=list,
        description="提及的行星",
    )

    # WA-02: 星座
    signs_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的星座",
    )

    # WA-03: 宫位 (Batch B T1.3-c: 迁移至 vocab runtime int 校验)
    houses_mentioned: list[int] = Field(
        default_factory=list,
        description="提及的宫位 (1-12)",
    )

    # WA-04: 相位
    aspects_mentioned: list[AspectMention] = Field(
        default_factory=list,
        description="提及的相位",
    )

    # WA-05 & WA-07: 过境/逆行
    is_transit_analysis: bool = Field(
        default=False,
        description="是否涉及过境分析",
    )
    retrograde_planets: list[str] = Field(
        default_factory=list,
        description="涉及的逆行行星",
    )

    # WA-09: 宫位制
    house_system: str | None = Field(
        default=None,
        description="使用的宫位制 (普拉西度制/整宫制等)",
    )

    # 分析类型 (Batch B T1.3-c: 迁移至 vocab runtime 校验，schema/vocab 已对齐)
    # D02: nullable compatibility primary, with evidence-scoped multi-value mentions
    chart_type: str | None = Field(
        default=None,
        description="星盘类型",
    )

    # WA-10: 四元素 (Batch B T1.3-c: 迁移至 vocab runtime 校验)
    elements_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的元素 (火象/土象/风象/水象)",
    )

    # WA-11: 三模式
    modalities_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的模式 (开创/固定/变动)",
    )

    # WA-12: 尊贵/落陷
    dignities_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的尊贵状态 (入庙/擢升/落陷/弱势等)",
    )

    # WA-13: 恒星
    fixed_stars_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的恒星 (天狼星/老人星等)",
    )

    # WA-14: 月相
    lunar_phase: str | None = Field(
        default=None,
        description="提及的月相 (新月/眉月/上弦月/…)",
    )

    # WA-15: 分析类型 (T1.8: topics_mentioned → analysis_type_mentioned)
    analysis_type_mentioned: list[str] = Field(
        default_factory=list,
        description="提及的分析类型 (本命盘分析/过境分析/…)。T1.8: 原 topics_mentioned 已 rename。",
    )

    # Batch B T1.3-c: vocab runtime 校验
    _validate_houses_mentioned = field_validator("houses_mentioned", mode="before")(
        make_membership_validator("houses", "西占", value_type="int")
    )
    _validate_retrograde_planets = field_validator("retrograde_planets", mode="before")(
        make_membership_validator("planets", "西占")
    )
    _validate_chart_type = field_validator("chart_type", mode="before")(
        make_membership_validator("chart_types", "西占", expect_scalar=True)
    )
    _validate_elements_mentioned = field_validator("elements_mentioned", mode="before")(
        make_membership_validator("elements", "西占")
    )
    # T1.8: analysis_type_mentioned vocab runtime 校验
    _validate_analysis_type_mentioned = field_validator("analysis_type_mentioned", mode="before")(
        make_membership_validator("analysis_type", "西占")
    )


# ═══════════════════════════════════════════════════════════════
# 带领域扩展的完整标注记录 V2
# ═══════════════════════════════════════════════════════════════


def _reject_cjk_en(info: ValidationInfo) -> bool:
    """是否启用英文字段 CJK 硬拒（context-gated）。

    默认（无 validation context）= 宽松，让存量脏数据仍可被读取/重标/dedup/audit
    路径解析（见 plan-corpus-english-annotation-consumption-v1.md §2.5：299 条
    pre-shard 脏 chunk 清理前必须仍可读）。仅生产写出口显式传
    ``context={"reject_cjk_en": True}`` 时才硬拒，把 CJK 拦在写入边界。
    """
    ctx = info.context
    if ctx is None:
        return False
    return bool(ctx.get("reject_cjk_en"))


class AnnotatedChunkV2(BaseModel):
    """带领域扩展的完整标注记录 — 支持四流派专属字段。

    相比 AnnotatedChunk:
    - 新增 ziwei/liuyao/tarot/western 四个可选扩展字段
    - 根据 school 字段动态填充对应的扩展
    """

    # 基础字段 (与 AnnotatedChunk 相同)
    chunk_id: str = Field(..., pattern=r"^[a-z0-9_]+_c\d{3,}$")
    source_file: str = Field(..., min_length=1)
    page_range: list[int] = Field(..., min_length=1)
    chunk_text: str = Field(..., min_length=1)
    chunk_index: int = Field(..., ge=1)

    # 共享 Metadata 字段
    school: SchoolType
    content_type: Literal["概念解释", "实战断语", "案例分析", "古籍原文", "问答对话"]
    difficulty: Literal["easy", "medium", "hard"]
    summary: str = Field(..., max_length=300)
    summary_en: str = Field(default="", description="英文摘要，最多150个按空白分隔的词；超限重写")
    keywords: list[str] = Field(default_factory=list, max_length=10)
    keywords_en: list[str] = Field(default_factory=list, description="英文关键词 (可选)")
    topic: str | None = Field(default=None, description="主题分类 (如 命运总论、财运分析)")

    annotation_evidence: list[FieldEvidence] = Field(default_factory=list)
    retrieval_eligibility: RetrievalEligibility | None = Field(
        default=None, description="历史缺省兼容；新输出显式判断可检索、局部可用或仅归档"
    )

    # PR-3: 双语 + 流派代码 (与 ChunkMetadata 同步)
    content_language: Literal["zh", "en"] = Field(
        default="zh",
        description="语料语言 (zh=中文, en=英文)",
    )
    school_code: Literal["ziwei", "liuyao", "western", "tarot", "generic"] = Field(
        default="generic",
        description="机器可读的流派代码，用于向量库 metadata filter",
    )

    # 领域扩展 (按 school 动态填充)
    ziwei: ZiweiExtension | None = Field(
        default=None,
        description="紫微斗数扩展 (school=紫微 时填充)",
    )
    liuyao: LiuyaoExtension | None = Field(
        default=None,
        description="六爻扩展 (school=六爻 时填充)",
    )
    tarot: TarotExtension | None = Field(
        default=None,
        description="塔罗扩展 (school=塔罗 时填充)",
    )
    western: WesternExtension | None = Field(
        default=None,
        description="西方占星扩展 (school=西占 时填充)",
    )

    # 关联字段
    associated_image_chunk_ids: list[str] = Field(
        default_factory=list,
        description="关联的图像 chunk ID",
    )

    # T1.4: payload 版本戳 (Batch B 新增)
    vocab_version: str | None = Field(
        default=None,
        description="vocab YAML 主版本快照（从 _meta.version 读取）",
    )
    school_vocab_version: str | None = Field(
        default=None,
        description="流派 vocab 版本快照",
    )
    annotation_version: str | None = Field(
        default=None,
        description="标注器版本（来自 astrology_corpus.annotation.ANNOTATION_VERSION）",
    )

    # Plan B: 回填脚本元数据足迹（来源/时间戳/方法），便于审计 trace
    # 形如 {"positions_mentioned": {"method": "regex_v1", "timestamp": "2026-04-17T..."}}
    annotation_metadata: dict[str, Any] | None = Field(
        default=None,
        description="回填脚本元数据足迹（trace 用），不参与检索，不影响 payload",
    )

    # 写入侧 CJK 守卫（context-gated）：英文字段含中文会污染 dense 嵌入 + BM25 sparse
    # （英文 query 永远命不中中文 token）。默认宽松不破坏存量读取，仅生产写出口传
    # context={"reject_cjk_en": True} 时硬拒。见 §2.5 真缺口 1。
    @field_validator("summary_en", mode="after")
    @classmethod
    def _guard_cjk_summary_en(cls, value: str, info: ValidationInfo) -> str:
        if _reject_cjk_en(info) and contains_cjk(value):
            raise ValueError("summary_en 含 CJK 字符（英文字段须无中文）：应重译而非复制中文 surface")
        return value

    @field_validator("keywords_en", mode="after")
    @classmethod
    def _guard_cjk_keywords_en(cls, value: list[str], info: ValidationInfo) -> list[str]:
        if _reject_cjk_en(info):
            bad = [k for k in value if contains_cjk(k)]
            if bad:
                raise ValueError(f"keywords_en 含 CJK 条目（英文字段须无中文）: {bad!r}")
        return value


# ═══════════════════════════════════════════════════════════════
# T4.F2 CardCombinationChunkV2 — 塔罗牌组合 chunk
# ═══════════════════════════════════════════════════════════════


class CardCombinationChunkV2(BaseModel):
    """塔罗牌组合 chunk — 由 combination_chunker 从 tarot_vocabulary.yaml 生成。

    chunk_type 固定为 "card_combination"，以区别于普通文本 chunk。
    school / school_code / content_language 三字段默认填充塔罗元数据，
    确保 Qdrant 过滤时不走 generic fallback 或泄漏到其他流派。
    """

    chunk_id: str = Field(
        ...,
        description="唯一标识符，格式: tarot_combo_{index:04d}",
    )
    chunk_type: Literal["card_combination"] = Field(
        default="card_combination",
        description="块类型，固定为 'card_combination'",
    )
    chunk_text: str = Field(
        ...,
        min_length=1,
        description="中英双语 chunk 文本",
    )
    source_file: str = Field(
        default="tarot_vocabulary.yaml",
        description="来源文件（vocab YAML）",
    )

    # 塔罗组合内容
    card_combinations_mentioned: list[list[str]] = Field(
        default_factory=list,
        description="牌组合列表，每项为一组牌名",
    )
    en_cards_mentioned: list[str] = Field(
        default_factory=list,
        description="英文牌名列表",
    )
    theme: str | None = Field(default=None, description="中文主题标签")
    en_theme: str | None = Field(default=None, description="英文主题标签")

    # Fix 3 (P2): 显式 school metadata，防止走 generic fallback + 流派泄漏
    # school / school_code 来自 SCHOOL_TO_CODE SoT，不属于 vocab YAML canonical 值，
    # 故无需 make_membership_validator；content_language="mixed" 表示中英双语。
    school: str = Field(
        default="塔罗",
        description="所属流派（中文标签）",
    )
    school_code: str = Field(
        default="tarot",
        description="机器可读流派代码",
    )
    content_language: str = Field(
        default="mixed",
        description="语料语言（中英双语 = mixed）",
    )

    # 版本戳 (SoT Contract: Payload 必带版本戳)
    vocab_version: str | None = Field(
        default=None,
        description="vocab YAML 主版本快照",
    )
    school_vocab_version: str | None = Field(
        default=None,
        description="塔罗 vocab 版本快照",
    )
    annotation_version: str | None = Field(
        default=None,
        description="combination_chunker 版本戳",
    )
