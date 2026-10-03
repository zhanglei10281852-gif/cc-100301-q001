from __future__ import annotations

from pydantic import BaseModel, Field


class DepartmentCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    manager: str = Field(min_length=1, max_length=80)
    phone: str = Field(min_length=3, max_length=40)


class DepartmentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    manager: str | None = Field(default=None, min_length=1, max_length=80)
    phone: str | None = Field(default=None, min_length=3, max_length=40)
    is_active: bool | None = None


class MembershipRequest(BaseModel):
    department_id: int = Field(gt=0)
    title: str = Field(default="", max_length=120)
    is_primary: bool = False
    starts_at: str
    ends_at: str | None = None


class MembershipEndRequest(BaseModel):
    ends_at: str

