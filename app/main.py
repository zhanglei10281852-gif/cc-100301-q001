from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import audit, auth, departments_admin, maintenance, roles, system, users
from app.catalog.router import router as catalog_router
from app.core.errors import DomainError
from app.database import close_connection, init_db
from app.pilots.router import router as pilot_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    del app
    init_db()
    yield
    close_connection()


app = FastAPI(title="多城深度旅行运营服务", version="2.0.0", lifespan=lifespan)


@app.exception_handler(DomainError)
async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    del request
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "context": exc.context}},
    )


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(roles.router)
app.include_router(audit.router)
app.include_router(system.router)
app.include_router(departments_admin.router)
app.include_router(maintenance.router)
app.include_router(catalog_router)
app.include_router(pilot_router)


@app.get("/")
def root() -> dict:
    return {"service": "多城深度旅行运营服务", "version": "2.0.0"}

