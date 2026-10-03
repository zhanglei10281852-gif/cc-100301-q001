from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import current_principal
from app.core.config import Settings
from app.core.security import Principal
from app.database import get_connection, transaction
from app.services.maintenance import MaintenanceService

router = APIRouter(prefix="/api/maintenance", tags=["数据维护"])


@router.get("/config")
def configuration(principal: Principal = Depends(current_principal)) -> dict:
    principal.require("audit.read")
    return Settings.load().public_view()


@router.get("/diagnostics")
def diagnostics(principal: Principal = Depends(current_principal)) -> dict:
    return MaintenanceService(get_connection()).database_diagnostics(principal)


@router.get("/backup-manifest")
def backup_manifest(principal: Principal = Depends(current_principal)) -> dict:
    return MaintenanceService(get_connection()).manifest(principal)


@router.post("/prune/sessions")
def prune_sessions(principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return MaintenanceService(connection).prune_expired_sessions(principal)


@router.post("/prune/audit")
def prune_audit(principal: Principal = Depends(current_principal)) -> dict:
    with transaction(immediate=True) as connection:
        return MaintenanceService(connection).prune_audit(principal)
