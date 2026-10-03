from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import current_principal
from app.core.pagination import Page, page_result
from app.core.security import Principal
from app.database import get_connection, transaction
from app.repositories.identity import UserRepository
from app.schemas.identity import RoleAssignment, UserCreate, UserUpdate
from app.services.identity import IdentityService

router = APIRouter(prefix="/api/users", tags=["用户管理"])


@router.post("", status_code=201)
def create_user(data: UserCreate, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return IdentityService(connection).create_user(principal, data.model_dump())


@router.get("")
def list_users(
    status: str | None = None,
    department_id: int | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(current_principal),
) -> dict:
    principal.require("users.read")
    pagination = Page(page, size)
    repository = UserRepository(get_connection())
    conditions: list[str] = []
    params: list = []
    if status:
        conditions.append("status=?")
        params.append(status)
    if department_id is not None:
        conditions.append("department_id=?")
        params.append(department_id)
    rows = repository.list(status=status, department_id=department_id, limit=size, offset=pagination.offset)
    for row in rows:
        row.pop("password_hash", None)
    return page_result(total=repository.count(" AND ".join(conditions), tuple(params)), page=pagination, rows=rows)


@router.get("/{user_id}")
def get_user(user_id: int, principal: Principal = Depends(current_principal)) -> dict:
    principal.require("users.read")
    return IdentityService(get_connection()).detail(user_id)


@router.patch("/{user_id}")
def update_user(user_id: int, data: UserUpdate, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return IdentityService(connection).update_user(principal, user_id, data.model_dump(exclude_unset=True))


@router.put("/{user_id}/roles")
def replace_user_roles(user_id: int, data: RoleAssignment, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return IdentityService(connection).assign_roles(principal, user_id, data.role_codes)
