from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import current_principal
from app.core.pagination import Page, page_result
from app.core.security import Principal
from app.database import get_connection, transaction
from app.repositories.business import DepartmentRepository
from app.schemas.business import DepartmentCreateRequest, DepartmentUpdateRequest, MembershipEndRequest, MembershipRequest
from app.services.departments import DepartmentService

router = APIRouter(prefix="/api/departments", tags=["部门成员"])


@router.get("")
def list_departments(
    active_only: bool = True,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(current_principal),
) -> dict:
    principal.require("departments.read")
    pagination = Page(page, size)
    repository = DepartmentRepository(get_connection())
    where = "is_active=1" if active_only else ""
    rows = repository.list(active_only=active_only, limit=size, offset=pagination.offset)
    return page_result(total=repository.count(where), page=pagination, rows=rows)


@router.post("", status_code=201)
def create_department(data: DepartmentCreateRequest, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return DepartmentService(connection).create(principal, data.model_dump())


@router.patch("/{department_id}")
def update_department(department_id: int, data: DepartmentUpdateRequest, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return DepartmentService(connection).update(principal, department_id, data.model_dump(exclude_unset=True))


@router.get("/{department_id}/members")
def list_members(department_id: int, principal: Principal = Depends(current_principal)) -> list[dict]:
    principal.require("departments.read")
    from app.core.clock import to_storage, utc_now
    repository = DepartmentRepository(get_connection())
    repository.require(department_id)
    return repository.active_memberships(department_id, to_storage(utc_now()))


@router.post("/users/{user_id}/memberships", status_code=201)
def add_membership(user_id: int, data: MembershipRequest, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return DepartmentService(connection).add_membership(principal, user_id, data.model_dump())


@router.post("/memberships/{membership_id}/end")
def end_membership(membership_id: int, data: MembershipEndRequest, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return DepartmentService(connection).end_membership(principal, membership_id, data.ends_at)
