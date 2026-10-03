from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProductCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=160)
    organization: str = Field(min_length=2, max_length=160)
    origin_country: str = Field(min_length=2, max_length=80)
    category: Literal["章节线路", "县域慢游", "自然村寨", "非遗工坊", "夜游演艺", "交通联运"]
    intended_use: str = Field(min_length=10, max_length=2000)
    risk_level: Literal["low", "medium", "high"]
    regulatory_status: Literal["筹备", "试运营", "已发布", "暂停"] = "筹备"


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    intended_use: str | None = Field(default=None, min_length=10, max_length=2000)
    risk_level: Literal["low", "medium", "high"] | None = None
    regulatory_status: Literal["筹备", "试运营", "已发布", "暂停"] | None = None
    active: bool | None = None


class SiteCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]+$")
    name: str = Field(min_length=2, max_length=160)
    site_type: Literal["景区服务站", "县域接待点", "交通枢纽", "文化场馆", "社区合作点"]
    region: str = Field(min_length=2, max_length=120)
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    max_concurrent: int = Field(default=1, ge=1, le=10000)


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    region: str | None = Field(default=None, min_length=2, max_length=120)
    capabilities: list[str] | None = Field(default=None, max_length=100)
    max_concurrent: int | None = Field(default=None, ge=1, le=10000)
    status: Literal["active", "suspended", "closed"] | None = None


class EvidenceSubmit(BaseModel):
    product_code: str = Field(min_length=2, max_length=64)
    evidence_type: Literal["线路", "运力", "安全", "授权", "游览"]
    title: str = Field(min_length=2, max_length=200)
    source_name: str = Field(min_length=2, max_length=160)
    source_region: str = Field(min_length=2, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    content_digest: str = Field(min_length=16, max_length=128)
    summary: dict = Field(default_factory=dict)
    submitted_by: str = Field(min_length=1, max_length=120)


class EvidenceReview(BaseModel):
    reviewer: str = Field(min_length=1, max_length=120)
    decision: Literal["accepted", "rejected"]
    note: str = Field(default="", max_length=2000)


class FeedbackSubmit(BaseModel):
    product_code: str = Field(min_length=2, max_length=64)
    site_code: str = Field(min_length=2, max_length=64)
    session_reference: str = Field(min_length=2, max_length=120)
    audience_type: Literal["游客", "带队人员", "渠道伙伴", "社区居民"]
    rating: int = Field(ge=1, le=5)
    tags: list[str] = Field(default_factory=list, max_length=30)
    comment: str = Field(default="", max_length=2000)
    contact_digest: str = Field(default="", max_length=128)
    consent_to_follow_up: bool = False

    @model_validator(mode="after")
    def require_contact_for_follow_up(self) -> "FeedbackSubmit":
        if self.consent_to_follow_up and not self.contact_digest:
            raise ValueError("允许后续联系时必须提供联系人摘要")
        return self

