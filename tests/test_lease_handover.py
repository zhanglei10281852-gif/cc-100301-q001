"""租约围栏回归：失联、越界与恢复重演。

值班人员用受控时钟重演接待点失联场景，确认旧节点无法凭过期租约夺回控制权，
被拒绝的回执全部留痕，批次按唯一决定重新排队或继续执行。
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.clock import FrozenClock
from app.core.errors import ConflictError
from app.database import get_connection, init_db
from app.pilots.service import PilotOperationsService


PROTOCOL = {
    "code": "chapter-transfer",
    "name": "多城章节线路换乘节奏行程方案",
    "capability": "chapter-transfer",
    "parameter_schema": {"minutes": {"type": "integer", "required": True, "minimum": 1, "maximum": 30}},
    "default_parameters": {},
    "max_runtime_seconds": 300,
    "max_attempts": 2,
}

PAYLOAD = {
    "protocol_code": "chapter-transfer",
    "project_code": "golden-week-2026",
    "requested_by": "dispatch-1",
    "parameters": {"minutes": 8},
    "priority": 70,
    "idempotency_key": "handover-000001",
}


@pytest.fixture()
def service(client):
    init_db()
    clock = FrozenClock(datetime(2026, 10, 1, 8, 0, 0, tzinfo=UTC))
    operations = PilotOperationsService(get_connection(), clock)
    operations.create_protocol(PROTOCOL, "administrator")
    operations.clock = clock
    return operations


def submit(service: PilotOperationsService, key: str = "handover-000001", priority: int = 70) -> dict:
    return service.submit({**PAYLOAD, "idempotency_key": key, "priority": priority})


def rejection(service: PilotOperationsService, action: str, *args) -> ConflictError:
    with pytest.raises(ConflictError) as captured:
        getattr(service, action)(*args)
    return captured.value


def test_offline_node_cannot_renew_expired_lease_and_recovery_requeues(service):
    """失联→越界→恢复→备援接管→旧节点回执只能被记录和拒绝。"""
    session = submit(service)
    sid = session["id"]

    claimed = service.claim("qingzhou-site", ["chapter-transfer"], 60)
    assert claimed["lease_owner"] == "qingzhou-site"
    lease_until = claimed["lease_expires_at"]

    # 青州接待点失联，时钟越过租约边界。
    service.clock.advance(seconds=61)

    # 旧节点恢复上线，凭旧租约续租：拒绝并留痕，状态不被覆盖。
    error = rejection(service, "heartbeat", sid, "qingzhou-site", 60)
    assert error.context["violation"] == "lease_expired"
    assert error.context["actual_holder"] == "qingzhou-site"
    assert error.context["observed_version"] == 2
    assert error.context["trace_id"]
    held = service.get_session(sid)
    assert held["lease_expires_at"] == lease_until and held["version"] == 2

    # 超时恢复接管：批次重新排队，旧持有者被切断。
    assert service.recover_expired() == {"recovered": [sid], "exhausted": []}
    assert service.get_session(sid)["status"] == "queued"

    # 备援节点领取，成为唯一执行方。
    backup = service.claim("backup-site", ["chapter-transfer"], 60)
    assert backup["lease_owner"] == "backup-site"

    # 旧节点的续租、完成与失败回执全部被拒，且能看到实际持有者。
    for action, args in (
        ("heartbeat", (sid, "qingzhou-site", 60)),
        ("complete", (sid, "qingzhou-site", {"value": 1}, {})),
        ("fail", (sid, "qingzhou-site", "stale_node", "旧节点迟到上报", True)),
    ):
        error = rejection(service, action, *args)
        assert error.context["violation"] == "not_lease_holder"
        assert error.context["actual_holder"] == "backup-site"

    # 拒绝没有改写新节点状态：持有者、版本与租约保持原样。
    current = service.get_session(sid)
    assert current["status"] == "running"
    assert current["lease_owner"] == "backup-site"
    assert current["version"] == 4  # 提交→领取→恢复→备援领取，拒绝不递增版本

    # 备援节点正常续租并完成，批次按唯一决定继续执行。
    renewed = service.heartbeat(sid, "backup-site", 60)
    assert renewed["lease_expires_at"] > lease_until
    done = service.complete(sid, "backup-site", {"value": 3.14}, {"seconds": 2})
    assert done["status"] == "succeeded"

    # 迟到的完成与重复回执同样只被记录和拒绝。
    for action, args in (
        ("complete", (sid, "qingzhou-site", {"value": 9}, {})),
        ("complete", (sid, "backup-site", {"value": 9}, {})),
        ("fail", (sid, "qingzhou-site", "stale_node", "旧节点迟到上报", True)),
    ):
        error = rejection(service, action, *args)
        assert error.context["violation"] == "session_not_running"
    assert service.get_session(sid)["status"] == "succeeded"

    # 交接轨迹完整：领取、被拒回执、超时恢复与备援接管全部可查。
    trace = [(item["actor"], item["action"]) for item in service.get_session(sid)["interventions"]]
    assert trace == [
        ("qingzhou-site", "claim"),
        ("qingzhou-site", "heartbeat_rejected"),
        ("recovery-site", "lease_recovery"),
        ("backup-site", "claim"),
        ("qingzhou-site", "heartbeat_rejected"),
        ("qingzhou-site", "complete_rejected"),
        ("qingzhou-site", "fail_rejected"),
        ("qingzhou-site", "complete_rejected"),
        ("backup-site", "complete_rejected"),
        ("qingzhou-site", "fail_rejected"),
    ]


def test_boundary_moment_allows_exactly_one_outcome(service):
    """超时恢复与旧节点续租在同一时刻竞争时，只允许一个结果落地。"""
    # 到期瞬间：续租被拒，恢复接管。
    first = submit(service, "boundary-000001")
    service.claim("qingzhou-site", ["chapter-transfer"], 60)
    service.clock.advance(seconds=60)  # 时钟正好停在租约到期时刻
    error = rejection(service, "heartbeat", first["id"], "qingzhou-site", 60)
    assert error.context["violation"] == "lease_expired"
    assert service.recover_expired() == {"recovered": [first["id"]], "exhausted": []}
    assert service.get_session(first["id"])["status"] == "queued"

    # 到期前一秒：续租成功，同一时刻恢复不接管。
    second = submit(service, "boundary-000002", priority=80)
    claimed = service.claim("qingzhou-site", ["chapter-transfer"], 60)
    assert claimed["id"] == second["id"]
    expires_at = claimed["lease_expires_at"]
    service.clock.advance(seconds=59)
    renewed = service.heartbeat(second["id"], "qingzhou-site", 60)
    assert renewed["lease_expires_at"] > expires_at
    assert service.recover_expired() == {"recovered": [], "exhausted": []}
    assert service.get_session(second["id"])["lease_owner"] == "qingzhou-site"

    # 恢复先落地的同一时刻：旧节点随后的续租同样被拒，结果与顺序无关。
    third = submit(service, "boundary-000003", priority=90)
    claimed = service.claim("qingzhou-site", ["chapter-transfer"], 30)
    assert claimed["id"] == third["id"]
    service.clock.advance(seconds=30)
    assert service.recover_expired() == {"recovered": [third["id"]], "exhausted": []}
    error = rejection(service, "heartbeat", third["id"], "qingzhou-site", 30)
    assert error.context["violation"] == "session_not_running"
    assert service.get_session(third["id"])["status"] == "queued"


def test_exhausted_attempts_fail_and_old_holder_stays_rejected(service):
    """达到最大尝试次数的批次转为失败，旧节点回执依旧只被记录。"""
    session = submit(service, "exhaust-000001")
    sid = session["id"]
    service.claim("qingzhou-site", ["chapter-transfer"], 20)
    service.clock.advance(seconds=21)
    assert service.recover_expired() == {"recovered": [sid], "exhausted": []}

    service.claim("qingzhou-site", ["chapter-transfer"], 20)
    service.clock.advance(seconds=21)
    assert service.recover_expired() == {"recovered": [], "exhausted": [sid]}
    assert service.get_session(sid)["status"] == "failed"

    for action, args in (
        ("heartbeat", (sid, "qingzhou-site", 20)),
        ("complete", (sid, "qingzhou-site", {"value": 1}, {})),
    ):
        error = rejection(service, action, *args)
        assert error.context["violation"] == "session_not_running"
    details = service.get_session(sid)
    assert details["status"] == "failed"
    assert [item["action"] for item in details["interventions"]].count("lease_recovery") == 2


def test_rejection_context_and_trace_visible_via_api(client):
    """值班人员通过接口看到实际持有者、观察版本、人工干预与交接轨迹。"""
    response = client.post("/api/pilots/protocols?actor=administrator", json=PROTOCOL)
    assert response.status_code == 201
    submitted = client.post("/api/pilots/sessions", json=PAYLOAD).json()
    sid = submitted["id"]
    claimed = client.post(
        "/api/pilots/sessions/claim",
        json={"site_code": "qingzhou-site", "capabilities": ["chapter-transfer"], "lease_seconds": 60},
    )
    assert claimed.status_code == 200

    # 非持有者续租：409 拒绝信息携带实际持有者与观察版本。
    conflict = client.post(
        f"/api/pilots/sessions/{sid}/heartbeat",
        json={"site_code": "intruder-site", "capabilities": [], "lease_seconds": 60},
    )
    assert conflict.status_code == 409
    context = conflict.json()["error"]["context"]
    assert context["violation"] == "not_lease_holder"
    assert context["actual_holder"] == "qingzhou-site"
    assert context["observed_version"] == 2
    assert context["receipt"] == "heartbeat"
    assert context["trace_id"]

    # 任务详情给出交接轨迹：领取与拒绝记录按序可查。
    details = client.get(f"/api/pilots/session-details/{sid}").json()
    assert details["lease_owner"] == "qingzhou-site"
    actions = [(item["actor"], item["action"]) for item in details["interventions"]]
    assert actions == [("qingzhou-site", "claim"), ("intruder-site", "heartbeat_rejected")]
