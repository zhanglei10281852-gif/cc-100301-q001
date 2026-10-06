from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.clock import FrozenClock
from app.database import close_connection, database_path, get_connection, init_db
from app.main import app
from app.pilots.service import LeaseRejectedError, PilotOperationsService


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def init_database() -> int:
    init_db()
    _print({"database": str(database_path()), "initialized": True})
    return 0


def check_database() -> int:
    init_db()
    connection = get_connection()
    _print({
        "database": str(database_path()),
        "integrity": connection.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_keys": connection.execute("PRAGMA foreign_keys").fetchone()[0],
        "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
        "schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
    })
    return 0


def smoke() -> int:
    with tempfile.TemporaryDirectory(prefix="travel-smoke-") as directory:
        os.environ["DEEP_TRAVEL_DATABASE_PATH"] = os.path.join(directory, "smoke.db")
        close_connection()
        with TestClient(app) as client:
            root = client.get("/")
            health = client.get("/api/system/health")
            if root.status_code != 200 or health.status_code != 200:
                _print({"root": root.text, "health": health.text})
                return 1
            _print({"root": root.json(), "health": health.json()})
        close_connection()
    return 0


def pilot_demo() -> int:
    with tempfile.TemporaryDirectory(prefix="travel-demo-") as directory:
        os.environ["DEEP_TRAVEL_DATABASE_PATH"] = os.path.join(directory, "demo.db")
        close_connection()
        with TestClient(app) as client:
            product = client.post("/api/catalog/products", json={
                "code": "novel-route-shandong",
                "name": "跟着网文章节游山东",
                "organization": "齐鲁深度旅行联合体",
                "origin_country": "中国",
                "category": "章节线路",
                "intended_use": "串联七座城市的网文场景、古城街区和在地体验，并记录跨城衔接运行情况",
                "risk_level": "medium",
                "regulatory_status": "试运营",
            })
            site = client.post("/api/catalog/sites", json={
                "code": "qingzhou-ancient-city",
                "name": "青州古城接待节点",
                "site_type": "县域接待点",
                "region": "山东青州",
                "capabilities": ["chapter-transfer"],
                "max_concurrent": 2,
            })
            protocol = client.post("/api/pilots/protocols?actor=demo", json={
                "code": "chapter-transfer",
                "name": "七城章节线路换乘节奏方案",
                "capability": "chapter-transfer",
                "parameter_schema": {"minutes": {"type": "integer", "required": True, "minimum": 1, "maximum": 30}},
                "default_parameters": {},
                "max_runtime_seconds": 1800,
                "max_attempts": 2,
            })
            submitted = client.post("/api/pilots/sessions", json={
                "protocol_code": "chapter-transfer",
                "project_code": "golden-week-2026",
                "requested_by": "operator-demo",
                "parameters": {"minutes": 8},
                "priority": 70,
                "idempotency_key": "demo-session-001",
            })
            claimed = client.post("/api/pilots/sessions/claim", json={"site_code": "qingzhou-ancient-city", "capabilities": ["chapter-transfer"], "lease_seconds": 60})
            values = [product, site, protocol, submitted, claimed]
            if any(response.status_code >= 400 for response in values):
                _print({"errors": [response.text for response in values]})
                return 1
            _print({"product": product.json()["code"], "site": site.json()["code"], "session": claimed.json()["session"]})
        close_connection()
    return 0


def lease_replay() -> int:
    """用受控时钟重演国庆首日失联、越界续租与超时移交全过程。"""
    protocol = {
        "code": "chapter-transfer",
        "name": "七城章节线路换乘节奏方案",
        "capability": "chapter-transfer",
        "parameter_schema": {"minutes": {"type": "integer", "required": True, "minimum": 1, "maximum": 30}},
        "default_parameters": {},
        "max_runtime_seconds": 1800,
        "max_attempts": 2,
    }
    trace: list[dict] = []

    def record(step: str, outcome: str, **detail: object) -> None:
        entry = {"step": step, "outcome": outcome, "at": str(clock.now()), **detail}
        trace.append(entry)
        _print(entry)

    def expect_rejected(step: str, action) -> None:
        try:
            action()
        except LeaseRejectedError as exc:
            record(step, "rejected", reason=exc.context["reason_code"], actual_holder=exc.context["actual_holder"], actual_generation=exc.context["actual_generation"], manual=exc.context["manual_intervention_required"])
        else:
            record(step, "unexpectedly_accepted")

    with tempfile.TemporaryDirectory(prefix="travel-lease-replay-") as directory:
        os.environ["DEEP_TRAVEL_DATABASE_PATH"] = os.path.join(directory, "replay.db")
        close_connection()
        init_db()
        clock = FrozenClock(datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
        service = PilotOperationsService(get_connection(), clock)

        service.create_protocol(protocol, "dispatch-desk")
        session = service.submit({
            "protocol_code": "chapter-transfer",
            "project_code": "golden-week-2026-qingzhou",
            "requested_by": "line-operator",
            "parameters": {"minutes": 8},
            "priority": 80,
            "idempotency_key": "qingzhou-batch-national-day-01",
        })
        sid = session["id"]
        original = service.claim("qingzhou-ancient-city", ["chapter-transfer"], 60)
        record("原节点领取批次", "accepted", holder=original["lease_owner"], generation=original["lease_generation"], expires_at=original["lease_expires_at"])

        # 国庆首日短暂失联：时钟越过租约期限。
        clock.advance(seconds=61)
        expect_rejected("失联后原节点凭旧租约续租", lambda: service.heartbeat(sid, "qingzhou-ancient-city", 1, 60))

        # 调度席超时移交，批次重新排队。
        recovered = service.recover_expired("dispatch-desk")
        record("调度席超时移交", "accepted", recovered=recovered["recovered"], exhausted=recovered["exhausted"])

        # 原节点恢复上线，继续用旧世代续租，被 fencing 拒绝。
        expect_rejected("原节点恢复上线再次续租", lambda: service.heartbeat(sid, "qingzhou-ancient-city", 1, 60))

        # 备援节点领取唯一决定。
        backup = service.claim("qingzhou-backup", ["chapter-transfer"], 60)
        record("备援节点领取批次", "accepted", holder=backup["lease_owner"], generation=backup["lease_generation"], handoff_from=backup["handoff_from"])

        # 原节点迟到的失败与完成回执：只记录、不覆盖。
        expect_rejected("原节点迟到失败回执", lambda: service.fail(sid, "qingzhou-ancient-city", 1, "network_lost", "网络短暂失联", True))
        expect_rejected("原节点迟到完成回执", lambda: service.complete(sid, "qingzhou-ancient-city", 1, {"rogue": True}, {}))

        # 备援节点合法完成。
        finished = service.complete(sid, "qingzhou-backup", backup["lease_generation"], {"progress": 1.0}, {"visitors": 36})
        record("备援节点完成回执", "accepted", status=finished["status"], observation_version=finished["current_observation_version"])

        # 终态已决出，旧节点再补完成回执也只能被记录和拒绝。
        expect_rejected("终态后旧节点重复完成回执", lambda: service.complete(sid, "qingzhou-ancient-city", 1, {"rogue": True}, {}))

        details = service.get_session(sid)
        summary = {
            "session_id": sid,
            "final_status": details["status"],
            "final_holder_events": [e for e in details["lease_events"] if e["accepted"]][-1],
            "lease_owner": details["lease_owner"],
            "observation_version": details["current_observation_version"],
            "rejected_requests": [
                {"step": index + 1, "type": event["type"], "site": event["site"], "reason": event["reason_code"]}
                for index, event in enumerate(e for e in details["lease_events"] if not e["accepted"])
            ],
            "handoff_trail": [(event["kind"], event["generation"]) for event in details["handoff_trail"]],
            "interventions": [item["action"] for item in details["interventions"]],
        }
        close_connection()

    _print({"replay_conclusion": summary})
    rejected_steps = [item for item in trace if item["outcome"] == "unexpectedly_accepted"]
    return 1 if rejected_steps else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="多城深度旅行运营服务命令行")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="初始化 SQLite 数据库")
    sub.add_parser("check-db", help="检查数据库完整性")
    sub.add_parser("smoke", help="进程内检查根路径和服务状态接口")
    sub.add_parser("pilot-demo", help="运行产品、节点、方案和行程任务演示")
    sub.add_parser("lease-replay", help="用受控时钟重演租约失联、越界续租与超时移交")
    return parser


def main(argv: list[str] | None = None) -> int:
    command = build_parser().parse_args(argv).command
    actions = {"init-db": init_database, "check-db": check_database, "smoke": smoke, "pilot-demo": pilot_demo, "lease-replay": lease_replay}
    try:
        return actions[command]()
    finally:
        close_connection()


if __name__ == "__main__":
    sys.exit(main())

