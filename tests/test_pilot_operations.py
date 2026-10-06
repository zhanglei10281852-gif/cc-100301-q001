from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from app.pilots.service import LeaseRejectedError, PilotOperationsService
from app.core.clock import FrozenClock
from app.database import close_connection, get_connection, init_db, transaction


PROTOCOL = {
    "code": "chapter-transfer",
    "name": "多城章节线路换乘节奏行程方案",
    "capability": "chapter-transfer",
    "parameter_schema": {
        "minutes": {"type": "integer", "required": True, "minimum": 1, "maximum": 30},
        "assist_level": {"type": "number", "required": False, "minimum": 0.0, "maximum": 1.0},
        "scene": {"type": "string", "required": True, "choices": ["rail", "coach"]},
    },
    "default_parameters": {"assist_level": 0.4},
    "max_runtime_seconds": 300,
    "max_attempts": 2,
}


def submit_payload(key: str, *, user: str = "pilot-operator-1", priority: int = 50) -> dict:
    return {
        "protocol_code": "chapter-transfer",
        "project_code": "seven-city-story-route",
        "requested_by": user,
        "parameters": {"minutes": 8, "scene": "rail"},
        "priority": priority,
        "idempotency_key": key,
    }


def create_protocol(client) -> None:
    response = client.post("/api/pilots/protocols?actor=administrator", json=PROTOCOL)
    assert response.status_code == 201, response.text


def test_protocol_submission_idempotency_and_parameter_validation(client):
    create_protocol(client)
    first = client.post("/api/pilots/sessions", json=submit_payload("request-000001"))
    second = client.post("/api/pilots/sessions", json=submit_payload("request-000001"))
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    invalid = submit_payload("request-000002")
    invalid["parameters"]["minutes"] = 50
    rejected = client.post("/api/pilots/sessions", json=invalid)
    assert rejected.status_code == 422


def test_priority_capability_claim_and_observation_version(client):
    create_protocol(client)
    low = client.post("/api/pilots/sessions", json=submit_payload("priority-low", priority=10)).json()
    high = client.post("/api/pilots/sessions", json=submit_payload("priority-high", priority=90)).json()
    no_match = client.post("/api/pilots/sessions/claim", json={"site_code": "w0", "capabilities": ["other"], "lease_seconds": 60})
    assert no_match.status_code == 200 and no_match.json()["session"] is None
    claimed = client.post("/api/pilots/sessions/claim", json={"site_code": "w1", "capabilities": ["chapter-transfer"], "lease_seconds": 60})
    assert claimed.status_code == 200
    assert claimed.json()["session"]["id"] == high["id"]
    completed = client.post(
        f"/api/pilots/sessions/{high['id']}/complete",
        json={"site_code": "w1", "lease_generation": 1, "observation": {"value": 3.14}, "metrics": {"seconds": 2}},
    )
    assert completed.status_code == 200
    details = client.get(f"/api/pilots/session-details/{high['id']}").json()
    assert details["status"] == "succeeded"
    assert details["current_observation_version"] == 1
    assert len(details["observations"]) == 1
    assert low["status"] == "queued"


def test_quota_cancel_retry_priority_and_batch_interventions(client):
    create_protocol(client)
    quota = client.put(
        "/api/pilots/quotas?actor=administrator",
        json={"subject_type": "user", "subject_key": "limited", "max_queued": 1, "max_running": 1, "daily_submissions": 2},
    )
    assert quota.status_code == 200
    one = client.post("/api/pilots/sessions", json=submit_payload("quota-one", user="limited")).json()
    blocked = client.post("/api/pilots/sessions", json=submit_payload("quota-two", user="limited"))
    assert blocked.status_code == 409
    cancelled = client.post(f"/api/pilots/sessions/{one['id']}/cancel", json={"actor": "administrator", "reason": "项目暂停"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    retried = client.post(f"/api/pilots/sessions/{one['id']}/retry", json={"actor": "administrator", "reason": "项目恢复", "priority": 95})
    assert retried.status_code == 200 and retried.json()["priority"] == 95
    other = client.post("/api/pilots/sessions", json=submit_payload("batch-other", user="other-user")).json()
    batch = client.post(
        "/api/pilots/sessions/batch",
        json={"session_ids": [one["id"], other["id"]], "operation": "priority", "actor": "administrator", "reason": "线路合作方临时到场", "priority": 99},
    )
    assert batch.status_code == 200
    assert len(batch.json()["succeeded"]) == 2
    details = client.get(f"/api/pilots/session-details/{one['id']}").json()
    assert [item["action"] for item in details["interventions"]] == ["cancel", "retry", "priority"]


def test_failure_backoff_and_expired_lease_recovery(client):
    from app.database import init_db

    init_db()
    clock = FrozenClock(datetime(2026, 9, 26, 2, 0, tzinfo=UTC))
    service = PilotOperationsService(get_connection(), clock)
    service.create_protocol(PROTOCOL, "administrator")
    first = service.submit(submit_payload("failure-000001"))
    claimed = service.claim("site-a", ["chapter-transfer"], 10)
    assert claimed and claimed["id"] == first["id"]
    failed = service.fail(first["id"], "site-a", 1, "connection_delayed", "前序列车晚点导致接驳窗口不稳定", True)
    assert failed["status"] == "queued"
    assert failed["available_at"] > failed["updated_at"]
    clock.advance(seconds=2)
    claimed_again = service.claim("site-a", ["chapter-transfer"], 10)
    assert claimed_again and claimed_again["attempt_count"] == 2
    clock.advance(seconds=11)
    recovered = service.recover_expired()
    assert recovered["exhausted"] == [first["id"]]
    details = service.get_session(first["id"])
    assert details["status"] == "failed"
    assert details["interventions"][-1]["action"] == "lease_recovery"



def _frozen_service(client, *, at: datetime | None = None):
    init_db()
    clock = FrozenClock(at or datetime(2026, 10, 1, 0, 0, tzinfo=UTC))
    service = PilotOperationsService(get_connection(), clock)
    service.create_protocol(PROTOCOL, "administrator")
    return service, clock


def test_expired_renewal_cannot_retake_lease(client):
    service, clock = _frozen_service(client)
    session = service.submit(submit_payload("lease-fence-0001"))
    claimed = service.claim("qingzhou-a", ["chapter-transfer"], 10)
    assert claimed["lease_generation"] == 1
    assert claimed["lease_owner"] == "qingzhou-a"

    # 失联超过租约：旧节点凭旧租约续租，必须被拒绝。
    clock.advance(seconds=11)
    with pytest.raises(LeaseRejectedError) as exc:
        service.heartbeat(session["id"], "qingzhou-a", 1, 10)
    assert exc.value.context["reason_code"] == "lease_expired"
    assert exc.value.context["actual_status"] == "running"

    # 调度席把批次移交给备援节点。
    recovered = service.recover_expired("dispatch-desk")
    assert recovered["recovered"] == [session["id"]]
    details = service.get_session(session["id"])
    assert details["status"] == "queued"
    assert details["lease_owner"] == ""
    assert details["lease_generation"] == 2
    assert details["handoff_from"] == "qingzhou-a"

    # 原节点恢复上线，仍用旧世代续租：记录并拒绝，不能夺回控制权。
    with pytest.raises(LeaseRejectedError) as exc:
        service.heartbeat(session["id"], "qingzhou-a", 1, 10)
    assert exc.value.context["reason_code"] == "stale_generation"

    backup = service.claim("qingzhou-b", ["chapter-transfer"], 10)
    assert backup["lease_owner"] == "qingzhou-b"
    assert backup["lease_generation"] == 3

    with pytest.raises(LeaseRejectedError) as exc:
        service.heartbeat(session["id"], "qingzhou-a", 1, 10)
    context = exc.value.context
    assert context["reason_code"] == "not_holder"
    assert context["actual_holder"] == "qingzhou-b"
    assert context["actual_generation"] == 3
    assert context["manual_intervention_required"] is True

    details = service.get_session(session["id"])
    rejected = [event for event in details["lease_events"] if not event["accepted"]]
    assert [event["type"] for event in rejected] == ["renewed", "renewed", "renewed"]
    assert [event["reason_code"] for event in rejected] == ["lease_expired", "stale_generation", "not_holder"]
    assert [(event["kind"], event["generation"]) for event in details["handoff_trail"]] == [
        ("granted", 1), ("recovery", 2), ("granted", 3),
    ]
    assert details["status"] == "running"
    assert details["lease_owner"] == "qingzhou-b"


def test_late_complete_and_fail_receipts_never_overwrite_new_holder(client):
    service, clock = _frozen_service(client)
    session = service.submit(submit_payload("lease-fence-0002"))
    service.claim("qingzhou-a", ["chapter-transfer"], 10)
    clock.advance(seconds=11)
    service.recover_expired("dispatch-desk")
    backup = service.claim("qingzhou-b", ["chapter-transfer"], 10)
    assert backup["lease_generation"] == 3

    # 旧持有者迟到的失败回执：被记录和拒绝，批次继续由备援执行。
    with pytest.raises(LeaseRejectedError) as exc:
        service.fail(session["id"], "qingzhou-a", 1, "network_lost", "接待点网络中断后恢复", True)
    context = exc.value.context
    assert context["reason_code"] == "not_holder"
    assert context["actual_holder"] == "qingzhou-b"
    assert context["actual_generation"] == 3
    assert service.get_session(session["id"])["status"] == "running"

    # 当前持有者合法完成。
    done = service.complete(session["id"], "qingzhou-b", 3, {"progress": 1.0}, {})
    assert done["status"] == "succeeded"
    assert done["current_observation_version"] == 1

    # 旧持有者迟到的完成回执：终态已决出，只能记录不能覆盖。
    with pytest.raises(LeaseRejectedError) as exc:
        service.complete(session["id"], "qingzhou-a", 1, {"rogue": True}, {})
    assert exc.value.context["reason_code"] == "duplicate_terminal"

    details = service.get_session(session["id"])
    assert details["status"] == "succeeded"
    assert len(details["observations"]) == 1
    assert details["observations"][0]["created_by"] == "qingzhou-b"
    rejected = [event for event in details["lease_events"] if not event["accepted"]]
    assert [event["reason_code"] for event in rejected] == ["not_holder", "duplicate_terminal"]


def test_observed_version_guard_blocks_future_and_duplicate_receipts(client):
    service, clock = _frozen_service(client)
    session = service.submit(submit_payload("lease-fence-0003"))
    service.claim("qingzhou-a", ["chapter-transfer"], 10)

    # 回执基于尚未产生的观察版本：拒绝且场次保持运行。
    with pytest.raises(LeaseRejectedError) as exc:
        service.complete(session["id"], "qingzhou-a", 1, {"step": 1}, {}, observed_version=9)
    assert exc.value.context["reason_code"] == "future_observation"
    assert service.get_session(session["id"])["status"] == "running"
    assert service.get_session(session["id"])["observations"] == []

    done = service.complete(session["id"], "qingzhou-a", 1, {"step": 1}, {})
    assert done["status"] == "succeeded"

    # 重复完成回执不能产生第二条观察记录。
    with pytest.raises(LeaseRejectedError) as exc:
        service.complete(session["id"], "qingzhou-a", 1, {"step": 2}, {})
    assert exc.value.context["reason_code"] == "duplicate_terminal"
    details = service.get_session(session["id"])
    assert len(details["observations"]) == 1
    assert details["current_observation_version"] == 1


def test_heartbeat_recovery_race_allows_only_one_outcome(client):
    from datetime import timedelta

    init_db()
    base = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    setup = PilotOperationsService(get_connection(), FrozenClock(base))
    setup.create_protocol(PROTOCOL, "administrator")
    session_ids: list[int] = []
    for index in range(10):
        submitted = setup.submit(submit_payload(f"lease-race-{index:06d}"))
        claimed = setup.claim("qingzhou-a", ["chapter-transfer"], 10)
        assert claimed["id"] == submitted["id"]
        session_ids.append(submitted["id"])

    barrier = threading.Barrier(2)
    renewal_outcomes: dict[int, str] = {}
    recovery_result: dict[str, list[int]] = {}

    def renew_worker() -> None:
        close_connection()
        service = PilotOperationsService(get_connection(), FrozenClock(base + timedelta(seconds=9)))
        barrier.wait()
        for sid in session_ids:
            try:
                service.heartbeat(sid, "qingzhou-a", 1, 10)
                renewal_outcomes[sid] = "renewed"
            except LeaseRejectedError:
                renewal_outcomes[sid] = "rejected"

    def recovery_worker() -> None:
        close_connection()
        service = PilotOperationsService(get_connection(), FrozenClock(base + timedelta(seconds=11)))
        barrier.wait()
        recovery_result.update(service.recover_expired("dispatch-desk"))

    renewer = threading.Thread(target=renew_worker)
    recoverer = threading.Thread(target=recovery_worker)
    renewer.start()
    recoverer.start()
    renewer.join()
    recoverer.join()

    close_connection()
    verifier = PilotOperationsService(get_connection(), FrozenClock(base + timedelta(seconds=11)))
    recovered_set = set(recovery_result["recovered"])
    # 互斥不变量：一个场次不可能既被续租成功又被超时恢复。
    for sid in session_ids:
        after = verifier.get_session(sid)
        if sid in recovered_set:
            assert renewal_outcomes[sid] == "rejected"
            assert after["status"] == "queued"
            assert after["lease_generation"] == 2
        else:
            assert renewal_outcomes[sid] == "renewed"
            assert after["status"] == "running"
            assert after["lease_owner"] == "qingzhou-a"
            assert after["lease_generation"] == 1


def test_api_rejection_payload_carries_handoff_context(client):
    create_protocol(client)
    session = client.post("/api/pilots/sessions", json=submit_payload("lease-fence-http-1")).json()
    claimed = client.post(
        "/api/pilots/sessions/claim",
        json={"site_code": "qingzhou-a", "capabilities": ["chapter-transfer"], "lease_seconds": 60},
    ).json()["session"]
    wrong_generation = claimed["lease_generation"] + 1
    response = client.post(
        f"/api/pilots/sessions/{session['id']}/heartbeat",
        json={"site_code": "qingzhou-a", "lease_generation": wrong_generation, "lease_seconds": 60},
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "lease_rejected"
    context = error["context"]
    assert context["reason_code"] == "stale_generation"
    assert context["actual_holder"] == "qingzhou-a"
    assert context["actual_generation"] == claimed["lease_generation"]
    assert "handoff_trail" in context
    assert "manual_interventions" in context
