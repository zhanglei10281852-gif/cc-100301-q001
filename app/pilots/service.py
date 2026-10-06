from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from app.pilots.repository import PilotRepository
from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.database import get_connection, transaction


def digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


class LeaseRejectedError(ConflictError):
    """旧持有者或过期请求被 fencing 规则拒绝，context 携带现场信息。"""

    code = "lease_rejected"


# 条件更新 WHERE 片段：必须同时是当前持有者、世代未失效、租约尚未到期。
_HOLDER_GUARD = (
    "status='running' AND lease_owner=? AND lease_generation=? "
    "AND lease_expires_at<>'' AND lease_expires_at>?"
)


class PilotOperationsService:
    """管理运营方案、配额、游览场次租约、观察记录版本和人工干预。"""

    def __init__(self, connection: sqlite3.Connection | None = None, clock: Clock | None = None) -> None:
        self.connection = connection or get_connection()
        self.clock = clock or SystemClock()
        self.repository = PilotRepository(self.connection)

    def list_protocols(self) -> list[dict[str, Any]]:
        return self.repository.active_protocols()

    def create_protocol(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        self._validate_schema(payload["parameter_schema"], payload["default_parameters"])
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = PilotRepository(connection)
            if repository.protocol_by_code(payload["code"]):
                raise ConflictError("参数方案编码已存在")
            return repository.create_protocol(
                code=payload["code"], name=payload["name"], capability=payload["capability"],
                parameter_schema=payload["parameter_schema"], defaults=payload["default_parameters"],
                max_runtime_seconds=payload["max_runtime_seconds"], max_attempts=payload["max_attempts"],
                created_by=actor, now=now,
            )

    def set_quota(self, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            return PilotRepository(connection).upsert_quota(actor=actor, now=now, **payload)

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        now_value = self.clock.now()
        now = to_storage(now_value)
        with transaction(immediate=True) as connection:
            repository = PilotRepository(connection)
            protocol = repository.protocol_by_code(payload["protocol_code"])
            if protocol is None or not protocol["active"]:
                raise NotFoundError("参数方案不存在或已经停用")
            parameters = self._validate_parameters(protocol, payload["parameters"])
            existing = repository.session_by_idempotency(payload["requested_by"], payload["idempotency_key"])
            parameter_digest = digest(parameters)
            if existing is not None:
                if existing["parameter_digest"] != parameter_digest:
                    raise ConflictError("同一幂等键对应了不同的运营参数")
                return dict(repository.session_by_id(existing["id"]))
            self._check_quota(repository, payload["requested_by"], now_value)
            return repository.create_session(
                protocol_id=protocol["id"], project_code=payload["project_code"],
                requested_by=payload["requested_by"], parameters=parameters,
                parameter_digest=parameter_digest, priority=payload["priority"],
                idempotency_key=payload["idempotency_key"], max_attempts=protocol["max_attempts"], now=now,
            )

    def list_sessions(self, *, status: str | None = None, project_code: str | None = None, requested_by: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_sessions(status=status, project_code=project_code, requested_by=requested_by, limit=max(1, min(limit, 500)))

    def get_session(self, session_id: int) -> dict[str, Any]:
        row = self.repository.session_by_id(session_id)
        if row is None:
            raise NotFoundError("运营游览场次不存在")
        observation = dict(row)
        events = self.repository.lease_events(session_id)
        observation["observations"] = self.repository.observation_versions(session_id)
        observation["interventions"] = self.repository.interventions(session_id)
        observation["lease_events"] = self._compact_events(events, limit=100)
        observation["handoff_trail"] = self._handoff_trail(events)
        return observation

    def claim(self, site_code: str, capabilities: list[str], lease_seconds: int) -> dict[str, Any] | None:
        now_value = self.clock.now()
        now = to_storage(now_value)
        lease_until = to_storage(now_value + timedelta(seconds=lease_seconds))
        with transaction(immediate=True) as connection:
            repository = PilotRepository(connection)
            candidate = repository.queued_candidate(capabilities, now)
            if candidate is None:
                return None
            previous_owner = candidate["handoff_from"]
            new_generation = int(candidate["lease_generation"]) + 1
            cursor = connection.execute(
                "UPDATE pilot_sessions SET status='running',attempt_count=attempt_count+1,lease_owner=?,lease_expires_at=?,"
                "lease_generation=?,started_at=COALESCE(started_at,?),updated_at=?,version=version+1 "
                "WHERE id=? AND status='queued'",
                (site_code, lease_until, new_generation, now, now, candidate["id"]),
            )
            if cursor.rowcount != 1:
                return None
            repository.add_lease_event(
                session_id=candidate["id"], event_type="granted", actor=site_code, generation=new_generation,
                accepted=True, detail=(f"从 {previous_owner} 交接后重新领取" if previous_owner else "首次领取"), now=now,
            )
            return dict(repository.session_by_id(candidate["id"]))

    def heartbeat(self, session_id: int, site_code: str, lease_generation: int, lease_seconds: int) -> dict[str, Any]:
        now_value = self.clock.now()
        now = to_storage(now_value)
        expires = to_storage(now_value + timedelta(seconds=lease_seconds))
        rejected: LeaseRejectedError | None = None
        result: dict[str, Any] | None = None
        with transaction(immediate=True) as connection:
            repository = PilotRepository(connection)
            session = repository.session_by_id(session_id)
            if session is None:
                raise NotFoundError("运营游览场次不存在")
            # 续租必须同时满足：租约尚未到期 + 节点仍是当前持有者 + 世代令牌一致。
            cursor = connection.execute(
                "UPDATE pilot_sessions SET lease_expires_at=?,updated_at=?,version=version+1 "
                f"WHERE id=? AND {_HOLDER_GUARD}",
                (expires, now, session_id, site_code, lease_generation, now),
            )
            if cursor.rowcount == 1:
                repository.add_lease_event(
                    session_id=session_id, event_type="renewed", actor=site_code, generation=lease_generation,
                    accepted=True, now=now,
                )
                result = dict(repository.session_by_id(session_id))
            else:
                reason = self._reject_reason(session, site_code, lease_generation, now)
                rejected = self._record_rejection(
                    repository, session, event_type="renewed", requester=site_code,
                    request_generation=lease_generation, observed_version=None,
                    reason=reason, detail=self._reject_detail("续租", reason, site_code, session), now=now,
                )
        if rejected is not None:
            raise rejected
        return result  # type: ignore[return-value]

    def complete(self, session_id: int, site_code: str, lease_generation: int, observation: dict[str, Any], metrics: dict[str, Any], observed_version: int | None = None) -> dict[str, Any]:
        now_value = self.clock.now()
        now = to_storage(now_value)
        rejected: LeaseRejectedError | None = None
        result: dict[str, Any] | None = None
        with transaction(immediate=True) as connection:
            repository = PilotRepository(connection)
            session = repository.session_by_id(session_id)
            if session is None:
                raise NotFoundError("运营游览场次不存在")
            version_reason = self._version_reason(session, observed_version)
            if version_reason is not None:
                rejected = self._record_rejection(
                    repository, session, event_type="completed", requester=site_code,
                    request_generation=lease_generation, observed_version=observed_version,
                    reason=version_reason, detail=self._reject_detail("完成回执", version_reason, site_code, session), now=now,
                )
            else:
                version = int(connection.execute("SELECT COALESCE(MAX(version),0)+1 FROM pilot_observations WHERE session_id=?", (session_id,)).fetchone()[0])
                # 先做 fencing 条件更新，确保观察记录只在持有者合法时落库。
                cursor = connection.execute(
                    "UPDATE pilot_sessions SET status='succeeded',current_observation_version=?,lease_owner='',lease_expires_at='',"
                    f"finished_at=?,updated_at=?,version=version+1 WHERE id=? AND {_HOLDER_GUARD}",
                    (version, now, now, session_id, site_code, lease_generation, now),
                )
                if cursor.rowcount == 1:
                    connection.execute(
                        "INSERT INTO pilot_observations(session_id,version,observation_json,metrics_json,observation_digest,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                        (session_id, version, json.dumps(observation, ensure_ascii=False, sort_keys=True), json.dumps(metrics, ensure_ascii=False, sort_keys=True), digest({"observation": observation, "metrics": metrics}), site_code, now),
                    )
                    repository.add_lease_event(
                        session_id=session_id, event_type="completed", actor=site_code, generation=lease_generation,
                        observed_version=version, accepted=True, now=now,
                    )
                    result = dict(repository.session_by_id(session_id))
                else:
                    reason = self._reject_reason(session, site_code, lease_generation, now)
                    rejected = self._record_rejection(
                        repository, session, event_type="completed", requester=site_code,
                        request_generation=lease_generation, observed_version=observed_version,
                        reason=reason, detail=self._reject_detail("完成回执", reason, site_code, session), now=now,
                    )
        if rejected is not None:
            raise rejected
        return result  # type: ignore[return-value]

    def fail(self, session_id: int, site_code: str, lease_generation: int, error_code: str, message: str, retryable: bool, observed_version: int | None = None) -> dict[str, Any]:
        now_value = self.clock.now()
        now = to_storage(now_value)
        rejected: LeaseRejectedError | None = None
        result: dict[str, Any] | None = None
        with transaction(immediate=True) as connection:
            repository = PilotRepository(connection)
            session = repository.session_by_id(session_id)
            if session is None:
                raise NotFoundError("运营游览场次不存在")
            version_reason = self._version_reason(session, observed_version)
            if version_reason is not None:
                rejected = self._record_rejection(
                    repository, session, event_type="failed", requester=site_code,
                    request_generation=lease_generation, observed_version=observed_version,
                    reason=version_reason, detail=self._reject_detail("失败回执", version_reason, site_code, session), now=now,
                )
            else:
                can_retry = retryable and int(session["attempt_count"]) < int(session["max_attempts"])
                status = "queued" if can_retry else "failed"
                delay = min(300, 2 ** max(0, int(session["attempt_count"]) - 1)) if can_retry else 0
                available = to_storage(now_value + timedelta(seconds=delay))
                cursor = connection.execute(
                    "UPDATE pilot_sessions SET status=?,available_at=?,lease_owner='',lease_expires_at='',handoff_from=?,last_error_code=?,"
                    f"last_error_message=?,finished_at=?,updated_at=?,version=version+1 WHERE id=? AND {_HOLDER_GUARD}",
                    (status, available, site_code, error_code, message[:2000], None if can_retry else now, now, session_id, site_code, lease_generation, now),
                )
                if cursor.rowcount == 1:
                    repository.add_lease_event(
                        session_id=session_id, event_type="failed", actor=site_code, generation=lease_generation,
                        observed_version=observed_version, accepted=True, reason_code=error_code, now=now,
                    )
                    result = dict(repository.session_by_id(session_id))
                else:
                    reason = self._reject_reason(session, site_code, lease_generation, now)
                    rejected = self._record_rejection(
                        repository, session, event_type="failed", requester=site_code,
                        request_generation=lease_generation, observed_version=observed_version,
                        reason=reason, detail=self._reject_detail("失败回执", reason, site_code, session), now=now,
                    )
        if rejected is not None:
            raise rejected
        return result  # type: ignore[return-value]

    def cancel(self, session_id: int, actor: str, reason: str, batch_key: str = "") -> dict[str, Any]:
        return self._intervene(session_id, actor, reason, "cancel", batch_key, self._cancel_mutation)

    def retry(self, session_id: int, actor: str, reason: str, priority: int | None = None, batch_key: str = "") -> dict[str, Any]:
        def mutate(connection: sqlite3.Connection, session: sqlite3.Row, now: str) -> None:
            if session["status"] not in {"failed", "cancelled"}:
                raise ConflictError("只有失败或已取消游览场次可以人工重试")
            chosen = session["priority"] if priority is None else priority
            connection.execute(
                "UPDATE pilot_sessions SET status='queued',priority=?,available_at=?,lease_owner='',lease_expires_at='' ,"
                "lease_generation=lease_generation+1,finished_at=NULL,updated_at=?,version=version+1 WHERE id=?",
                (chosen, now, now, session["id"]),
            )
        return self._intervene(session_id, actor, reason, "retry", batch_key, mutate)

    def set_priority(self, session_id: int, actor: str, reason: str, priority: int, batch_key: str = "") -> dict[str, Any]:
        def mutate(connection: sqlite3.Connection, session: sqlite3.Row, now: str) -> None:
            if session["status"] not in {"queued", "running"}:
                raise ConflictError("只有排队或运行中的游览场次可以调整优先级")
            connection.execute("UPDATE pilot_sessions SET priority=?,updated_at=?,version=version+1 WHERE id=?", (priority, now, session["id"]))
        return self._intervene(session_id, actor, reason, "priority", batch_key, mutate)

    def batch_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch_key = digest({"actor": payload["actor"], "session_ids": payload["session_ids"], "operation": payload["operation"], "reason": payload["reason"]})
        succeeded: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for session_id in list(dict.fromkeys(payload["session_ids"])):
            try:
                if payload["operation"] == "cancel":
                    value = self.cancel(session_id, payload["actor"], payload["reason"], batch_key)
                elif payload["operation"] == "retry":
                    value = self.retry(session_id, payload["actor"], payload["reason"], payload.get("priority"), batch_key)
                else:
                    value = self.set_priority(session_id, payload["actor"], payload["reason"], int(payload["priority"]), batch_key)
                succeeded.append({"session_id": session_id, "status": value["status"], "version": value["version"]})
            except (ConflictError, NotFoundError) as exc:
                failed.append({"session_id": session_id, "code": exc.code, "message": exc.message})
        return {"batch_key": batch_key, "succeeded": succeeded, "failed": failed}

    def recover_expired(self, actor: str = "recovery-site") -> dict[str, Any]:
        now = to_storage(self.clock.now())
        recovered: list[int] = []
        exhausted: list[int] = []
        with transaction(immediate=True) as connection:
            repository = PilotRepository(connection)
            # 条件更新保证：与旧节点续租同一时刻竞争时，只有一边的 WHERE 能命中。
            rows = connection.execute(
                "SELECT * FROM pilot_sessions WHERE status='running' AND lease_expires_at<>'' AND lease_expires_at<? ORDER BY id",
                (now,),
            ).fetchall()
            for session in rows:
                before = dict(session)
                if int(session["attempt_count"]) < int(session["max_attempts"]):
                    status, finished_at = "queued", None
                else:
                    status, finished_at = "failed", now
                previous_owner = session["lease_owner"]
                cursor = connection.execute(
                    "UPDATE pilot_sessions SET status=?,lease_owner='',lease_expires_at='',lease_generation=lease_generation+1,"
                    "handoff_from=?,available_at=?,last_error_code='lease_expired',last_error_message='执行站点租约已过期',"
                    "finished_at=?,updated_at=?,version=version+1 WHERE id=? AND status='running' AND lease_expires_at<>'' AND lease_expires_at<?",
                    (status, previous_owner, now, finished_at, now, session["id"], now),
                )
                if cursor.rowcount != 1:
                    # 并发竞争中旧持有者先一步续租成功：本场次不再恢复，保持其运行状态。
                    continue
                if status == "queued":
                    recovered.append(int(session["id"]))
                else:
                    exhausted.append(int(session["id"]))
                after = dict(repository.session_by_id(session["id"]))
                repository.add_lease_event(
                    session_id=session["id"], event_type="recovery", actor=actor, generation=int(after["lease_generation"]),
                    accepted=True, reason_code="lease_expired",
                    detail=f"{previous_owner} 失联超过租约期限，移交为 {status}", now=now,
                )
                repository.add_intervention(session_id=session["id"], actor=actor, action="lease_recovery", reason="租约过期自动恢复", before=before, after=after, batch_key="", now=now)
        return {"recovered": recovered, "exhausted": exhausted}

    def summary(self) -> dict[str, Any]:
        rows = self.connection.execute("SELECT status,COUNT(*) AS amount FROM pilot_sessions GROUP BY status ORDER BY status").fetchall()
        oldest = self.connection.execute("SELECT MIN(created_at) FROM pilot_sessions WHERE status='queued'").fetchone()[0]
        return {"states": {row["status"]: row["amount"] for row in rows}, "oldest_queued_at": oldest, "protocols": len(self.repository.active_protocols())}

    # ------------------------------------------------------------------
    # 租约 fencing 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _reject_reason(session: sqlite3.Row, requester: str, request_generation: int, now: str) -> str:
        """条件更新未命中时判定具体拒绝原因（调用时持 IMMEDIATE 事务锁，读到的是最新已提交状态）。"""
        if session["status"] == "running":
            if not session["lease_expires_at"] or session["lease_expires_at"] <= now:
                return "lease_expired"
            if session["lease_owner"] != requester:
                return "not_holder"
            if int(session["lease_generation"]) != int(request_generation):
                return "stale_generation"
            return "not_holder"
        if session["status"] == "queued":
            # 租约已被恢复并重新排队，旧持有者世代已经失效。
            return "stale_generation"
        return "duplicate_terminal"

    @staticmethod
    def _version_reason(session: sqlite3.Row, observed_version: int | None) -> str | None:
        """回执必须基于场次当前观察版本，迟到或重复回执不能覆盖新节点的状态。"""
        if observed_version is None:
            return None
        current = session["current_observation_version"]
        if current is not None and int(observed_version) < int(current):
            return "stale_observation"
        if int(observed_version) > int(current or 0):
            return "future_observation"
        return None

    @staticmethod
    def _reject_detail(action: str, reason: str, requester: str, session: sqlite3.Row) -> str:
        messages = {
            "lease_expired": f"{requester} 的租约已过期，{action}被拒绝",
            "not_holder": f"{requester} 不是当前持有者（实际持有者：{session['lease_owner'] or '无，已重新排队'}），{action}被拒绝",
            "stale_generation": f"{requester} 持有的是旧世代租约，场次已经移交，{action}被拒绝",
            "stale_observation": f"回执基于旧观察版本，当前版本为 {session['current_observation_version']}，{action}被拒绝",
            "future_observation": "回执观察版本超出当前场次版本，被拒绝",
            "duplicate_terminal": f"场次当前状态为 {session['status']}，迟到或重复的{action}只能记录不能覆盖新节点的状态",
        }
        return messages[reason]

    def _record_rejection(
        self, repository: PilotRepository, session: sqlite3.Row, *, event_type: str, requester: str,
        request_generation: int, observed_version: int | None, reason: str, detail: str, now: str,
    ) -> LeaseRejectedError:
        repository.add_lease_event(
            session_id=session["id"], event_type=event_type, actor=requester, generation=request_generation,
            observed_version=observed_version, accepted=False, reason_code=reason, detail=detail,
            actual_owner=session["lease_owner"], actual_generation=int(session["lease_generation"]), now=now,
        )
        events = repository.lease_events(session["id"])
        interventions = repository.interventions(session["id"])
        needs_manual = reason in {"lease_expired", "not_holder", "stale_generation", "stale_observation"}
        context = {
            "session_id": session["id"],
            "rejected": True,
            "reason_code": reason,
            "requester": requester,
            "request_generation": request_generation,
            "observed_version": observed_version,
            "actual_holder": session["lease_owner"],
            "actual_generation": int(session["lease_generation"]),
            "actual_status": session["status"],
            "current_observation_version": session["current_observation_version"],
            "manual_intervention_required": needs_manual,
            "manual_interventions": [
                {"action": item["action"], "actor": item["actor"], "reason": item["reason"], "at": item["created_at"]}
                for item in interventions[-5:]
            ],
            "handoff_trail": self._handoff_trail(events),
            "recent_lease_events": self._compact_events(events),
        }
        return LeaseRejectedError(detail, context=context)

    @staticmethod
    def _compact_events(events: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
        return [
            {
                "seq": event["id"],
                "at": event["created_at"],
                "type": event["event_type"],
                "site": event["actor"],
                "generation": event["generation"],
                "observed_version": event["observed_version"],
                "accepted": bool(event["accepted"]),
                "reason_code": event["reason_code"],
                "detail": event["detail"],
                "actual_holder": event["actual_owner"],
                "actual_generation": event["actual_generation"],
            }
            for event in events[-limit:]
        ]

    @staticmethod
    def _handoff_trail(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        trail: list[dict[str, Any]] = []
        for event in events:
            if event["accepted"] and event["event_type"] in {"granted", "recovery"}:
                trail.append({
                    "at": event["created_at"],
                    "generation": event["generation"],
                    "holder": event["actor"] if event["event_type"] == "granted" else "",
                    "kind": event["event_type"],
                    "detail": event["detail"],
                })
        return trail

    def _intervene(self, session_id: int, actor: str, reason: str, action: str, batch_key: str, mutation: Callable[[sqlite3.Connection, sqlite3.Row, str], None]) -> dict[str, Any]:
        now = to_storage(self.clock.now())
        with transaction(immediate=True) as connection:
            repository = PilotRepository(connection)
            session = repository.session_by_id(session_id)
            if session is None:
                raise NotFoundError("运营游览场次不存在")
            before = dict(session)
            mutation(connection, session, now)
            after = dict(repository.session_by_id(session_id))
            repository.add_intervention(session_id=session_id, actor=actor, action=action, reason=reason, before=before, after=after, batch_key=batch_key, now=now)
            return after

    @staticmethod
    def _cancel_mutation(connection: sqlite3.Connection, session: sqlite3.Row, now: str) -> None:
        if session["status"] not in {"queued", "running"}:
            raise ConflictError("当前游览场次状态不允许取消")
        status = "cancel_requested" if session["status"] == "running" else "cancelled"
        connection.execute("UPDATE pilot_sessions SET status=?,finished_at=?,updated_at=?,version=version+1 WHERE id=?", (status, None if status == "cancel_requested" else now, now, session["id"]))

    def _check_quota(self, repository: PilotRepository, requested_by: str, now: datetime) -> None:
        quota = repository.quota("user", requested_by)
        if quota is None:
            return
        states = repository.count_user_states(requested_by)
        if states.get("queued", 0) >= int(quota["max_queued"]):
            raise ConflictError("用户排队游览场次配额已用尽")
        if states.get("running", 0) >= int(quota["max_running"]):
            raise ConflictError("用户运行游览场次配额已用尽")
        day_start = to_storage(now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0))
        if repository.count_user_submissions_since(requested_by, day_start) >= int(quota["daily_submissions"]):
            raise ConflictError("用户当日提交配额已用尽")

    @staticmethod
    def _validate_schema(schema: dict[str, dict[str, Any]], defaults: dict[str, Any]) -> None:
        if not schema:
            raise ValidationError("参数方案至少包含一个参数")
        allowed = {"integer", "number", "string", "boolean"}
        for name, rule in schema.items():
            if not name or not isinstance(rule, dict) or rule.get("type") not in allowed:
                raise ValidationError(f"参数 {name or '<empty>'} 的规则不合法")
        if set(defaults) - set(schema):
            raise ValidationError("默认值包含未声明参数")

    def _validate_parameters(self, protocol: sqlite3.Row, supplied: dict[str, Any]) -> dict[str, Any]:
        schema = json.loads(protocol["parameter_schema_json"])
        values = {**json.loads(protocol["default_parameters_json"]), **supplied}
        unknown = set(values) - set(schema)
        if unknown:
            raise ValidationError("包含方案未声明的参数", context={"parameters": sorted(unknown)})
        normalized: dict[str, Any] = {}
        for name, rule in schema.items():
            if name not in values:
                if rule.get("required"):
                    raise ValidationError(f"缺少必填参数：{name}")
                continue
            value = values[name]
            kind = rule["type"]
            valid = {"integer": isinstance(value, int) and not isinstance(value, bool), "number": isinstance(value, (int, float)) and not isinstance(value, bool), "string": isinstance(value, str), "boolean": isinstance(value, bool)}[kind]
            if not valid:
                raise ValidationError(f"参数 {name} 类型不正确")
            if rule.get("minimum") is not None and value < rule["minimum"]:
                raise ValidationError(f"参数 {name} 小于允许的最小值")
            if rule.get("maximum") is not None and value > rule["maximum"]:
                raise ValidationError(f"参数 {name} 大于允许的最大值")
            if rule.get("choices") and value not in rule["choices"]:
                raise ValidationError(f"参数 {name} 不在允许的选项中")
            normalized[name] = value
        return normalized
