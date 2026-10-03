from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import current_principal
from app.core.security import Principal
from app.database import get_connection, transaction
from app.services.jobs import JobService

router = APIRouter(prefix="/api/system", tags=["系统运维"])


@router.get("/health")
def health() -> dict:
    connection = get_connection()
    foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
    return {"status": "ok", "foreign_keys": foreign_keys, "journal_mode": journal_mode}


@router.post("/jobs/example", status_code=201)
def enqueue_example(principal: Principal = Depends(current_principal)) -> dict:
    principal.require("jobs.run")
    with transaction(immediate=True) as connection:
        return JobService(connection).enqueue("system.example", f"example:{principal.user_id}", {"actor": principal.user_id})
