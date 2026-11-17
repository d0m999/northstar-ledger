"""Evidence-scoped annotation values. Domain enums remain in vocabulary YAML."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AnnotationScope(BaseModel):
    scope_id: str = Field(min_length=1)
    kind: Literal["concept", "rule", "conditional", "case"]
    condition: str | None = None
    attribution: str | None = None

    @model_validator(mode="after")
    def conditional_has_condition(self) -> AnnotationScope:
        if self.kind == "conditional" and not (self.condition or "").strip():
            raise ValueError("conditional scope requires the original condition")
        return self


class EvidenceSpan(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self) -> EvidenceSpan:
        if self.end <= self.start:
            raise ValueError("evidence span must be nonempty and ordered")
        return self


class FieldEvidence(BaseModel):
    field_path: str = Field(min_length=1, pattern=r"^[a-z_]+(?:\.[a-z_0-9]+)*$")
    scope: AnnotationScope
    spans: list[EvidenceSpan] = Field(default_factory=list)
    basis: Literal["source", "derived"] = "source"
    usage: Literal["mentioned", "discussed", "used"] | None = None
    null_reason: Literal["not_mentioned", "not_applicable", "ambiguous", "multiple_values"] | None = None
    derivation_rule: str | None = None
    input_paths: list[str] = Field(default_factory=list)


class RelationParticipant(BaseModel):
    entity_type: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    location: str | None = None


class RelationMention(BaseModel):
    relation_type: str
    participants: list[RelationParticipant] = Field(min_length=2)
    scope_id: str = Field(min_length=1)
    negated: bool = False
    condition: str | None = None


class UnregisteredTerm(BaseModel):
    """Source surface retained for review; never silently promoted to a canonical ID."""

    surface: str = Field(min_length=1)
    proposed_category: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    english_surface: str = Field(min_length=1)
    scope_id: str = Field(min_length=1)


class TimeContextMention(BaseModel):
    time_context: str
    scope_id: str = Field(min_length=1)
    subject: str | None = None
    chart_type: str | None = None


class RetrievalEligibility(BaseModel):
    status: Literal["eligible", "limited", "archive_only"]
    reason: str | None = None
    usable_spans: list[EvidenceSpan] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_status(self) -> RetrievalEligibility:
        if self.status != "eligible" and not (self.reason or "").strip():
            raise ValueError("limited/archive_only requires an explicit reason")
        if (self.status == "limited") != bool(self.usable_spans):
            raise ValueError("only limited status requires nonempty usable_spans")
        return self


class SemanticExtension(BaseModel):
    knowledge_domains: list[str] = Field(default_factory=list)
    topics_mentioned: list[str] = Field(default_factory=list)
    analysis_methods: list[str] = Field(default_factory=list)
    relations: list[RelationMention] = Field(default_factory=list)
    unregistered_terms: list[UnregisteredTerm] = Field(default_factory=list)


class SpiritRoleMention(BaseModel):
    role: str
    six_relative: str | None = None
    ordinal: int | None = Field(default=None, ge=1, le=6)
    scope_id: str = Field(min_length=1)
    is_moving: bool | None = None


class SpreadMention(BaseModel):
    spread: str
    scope_id: str = Field(min_length=1)
    usage: Literal["mentioned", "discussed", "used"]
    explanation: Literal["complete", "partial", "not_explained", "unknown"] = "unknown"


class LayoutMention(BaseModel):
    scope_id: str = Field(min_length=1)
    layout_mode: str | None = None
    card_count: int | None = Field(default=None, ge=1)
