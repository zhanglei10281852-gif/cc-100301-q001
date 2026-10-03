from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    client_label: str = Field(default="", max_length=100)


class LoginResponse(BaseModel):
    token: str
    expires_at: str
    user: dict
    permissions: list[str]


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    department_id: int | None = None
    role_codes: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("role_codes")
    @classmethod
    def unique_roles(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("角色不能重复")
        return value


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    department_id: int | None = None
    status: Literal["active", "disabled", "locked"] | None = None


class RoleCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,63}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    permission_codes: list[str] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permission_codes: list[str] | None = None


class RoleAssignment(BaseModel):
    role_codes: list[str] = Field(min_length=1, max_length=20)


class DepartmentMembershipCreate(BaseModel):
    department_id: int
    title: str = Field(default="", max_length=100)
    is_primary: bool = False
    starts_at: str
    ends_at: str | None = None
