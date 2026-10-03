from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import current_principal
from app.core.pagination import Page, page_result
from app.core.security import Principal
from app.database import get_connection
from app.repositories.audit import AuditRepository

router = APIRouter(prefix="/api/audit", tags=["审计记录"])


@router.get("")
def list_audit_events(
    actor_user_id: int | None = None,
    resource_type: str | None = None,
    action: str | None = None,
    outcome: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(current_principal),
) -> dict:
    principal.require("audit.read")
    pagination = Page(page, size)
    repository = AuditRepository(get_connection())
    rows = repository.list(
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        action=action,
        outcome=outcome,
        limit=size,
        offset=pagination.offset,
    )
    conditions: list[str] = []
    params: list = []
    for column, value in (("actor_user_id", actor_user_id), ("resource_type", resource_type), ("action", action), ("outcome", outcome)):
        if value is not None:
            conditions.append(f"{column}=?")
            params.append(value)
    query = "SELECT COUNT(*) FROM audit_events" + (" WHERE " + " AND ".join(conditions) if conditions else "")
    total = int(get_connection().execute(query, tuple(params)).fetchone()[0])
    return page_result(total=total, page=pagination, rows=rows)
