from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import current_principal
from app.core.security import Principal
from app.database import get_connection, transaction
from app.repositories.base import rows_dict
from app.repositories.identity import RoleRepository
from app.schemas.identity import RoleCreate, RoleUpdate
from app.services.identity import IdentityService

router = APIRouter(prefix="/api/roles", tags=["角色权限"])


@router.get("")
def list_roles(principal: Principal = Depends(current_principal)) -> list[dict]:
    principal.require("roles.read")
    repository = RoleRepository(get_connection())
    roles = repository.list()
    for role in roles:
        role["permissions"] = repository.permissions(role["id"])
    return roles


@router.get("/permissions")
def list_permissions(principal: Principal = Depends(current_principal)) -> list[dict]:
    principal.require("roles.read")
    return rows_dict(get_connection().execute("SELECT * FROM permissions ORDER BY resource,action").fetchall())


@router.post("", status_code=201)
def create_role(data: RoleCreate, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return IdentityService(connection).create_role(principal, data.model_dump())


@router.get("/{role_id}")
def get_role(role_id: int, principal: Principal = Depends(current_principal)) -> dict:
    principal.require("roles.read")
    return IdentityService(get_connection()).role_detail(role_id)


@router.patch("/{role_id}")
def update_role(role_id: int, data: RoleUpdate, principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return IdentityService(connection).update_role(principal, role_id, data.model_dump(exclude_unset=True))
