from __future__ import annotations

from fastapi import APIRouter, Query

from app.pilots.schemas import BatchOperation, CancelRequest, PriorityRequest, QuotaSet, RetryRequest, SessionClaim, SessionFailure, SessionHeartbeat, SessionObservation, SessionSubmit, ProtocolCreate
from app.pilots.service import PilotOperationsService

router = APIRouter(prefix="/api/pilots", tags=["深度旅行运营游览场次运营"])


def service() -> PilotOperationsService:
    return PilotOperationsService()


@router.get("/protocols")
def list_protocols():
    return {"items": service().list_protocols()}


@router.post("/protocols", status_code=201)
def create_protocol(payload: ProtocolCreate, actor: str = Query(..., min_length=1)):
    return service().create_protocol(payload.model_dump(), actor)


@router.put("/quotas")
def set_quota(payload: QuotaSet, actor: str = Query(..., min_length=1)):
    return service().set_quota(payload.model_dump(), actor)


@router.post("/sessions", status_code=202)
def submit_session(payload: SessionSubmit):
    return service().submit(payload.model_dump())


@router.get("/sessions")
def list_sessions(status: str | None = None, project_code: str | None = None, requested_by: str | None = None, limit: int = Query(default=100, ge=1, le=500)):
    return {"items": service().list_sessions(status=status, project_code=project_code, requested_by=requested_by, limit=limit)}


@router.get("/session-details/{session_id}")
def get_session(session_id: int):
    return service().get_session(session_id)


@router.post("/sessions/claim")
def claim_session(payload: SessionClaim):
    return {"session": service().claim(payload.site_code, payload.capabilities, payload.lease_seconds)}


@router.post("/sessions/{session_id}/heartbeat")
def heartbeat(session_id: int, payload: SessionHeartbeat):
    return service().heartbeat(session_id, payload.site_code, payload.lease_generation, payload.lease_seconds)


@router.post("/sessions/{session_id}/complete")
def complete_session(session_id: int, payload: SessionObservation):
    return service().complete(session_id, payload.site_code, payload.lease_generation, payload.observation, payload.metrics, payload.observed_version)


@router.post("/sessions/{session_id}/fail")
def fail_session(session_id: int, payload: SessionFailure):
    return service().fail(session_id, payload.site_code, payload.lease_generation, payload.error_code, payload.message, payload.retryable, payload.observed_version)


@router.post("/sessions/{session_id}/cancel")
def cancel_session(session_id: int, payload: CancelRequest):
    return service().cancel(session_id, payload.actor, payload.reason)


@router.post("/sessions/{session_id}/retry")
def retry_session(session_id: int, payload: RetryRequest):
    return service().retry(session_id, payload.actor, payload.reason, payload.priority)


@router.post("/sessions/{session_id}/priority")
def set_priority(session_id: int, payload: PriorityRequest):
    return service().set_priority(session_id, payload.actor, payload.reason, payload.priority)


@router.post("/sessions/batch")
def batch_operation(payload: BatchOperation):
    return service().batch_operation(payload.model_dump())


@router.post("/recovery/expired-leases")
def recover_expired(actor: str = Query(default="recovery-site", min_length=1)):
    return service().recover_expired(actor)


@router.get("/summary")
def summary():
    return service().summary()


