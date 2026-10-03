from __future__ import annotations

import sqlite3

from app.core.clock import Clock, SystemClock, from_storage, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import Principal
from app.repositories.business import DepartmentRepository
from app.repositories.identity import UserRepository
from app.services.audit import AuditContext, AuditService


class DepartmentService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.connection = connection
        self.clock = clock or SystemClock()
        self.departments = DepartmentRepository(connection)
        self.users = UserRepository(connection)
        self.audit = AuditService(connection, self.clock)

    def create(self, principal: Principal, data: dict) -> dict:
        principal.require("departments.write")
        name = data["name"].strip()
        if self.departments.by_name(name):
            raise ConflictError("部门名称已存在")
        now = to_storage(self.clock.now())
        cursor = self.connection.execute(
            "INSERT INTO departments(name,manager,phone,is_active,created_at,updated_at) VALUES(?,?,?,1,?,?)",
            (name, data["manager"].strip(), data["phone"].strip(), now, now),
        )
        created = self.departments.require(int(cursor.lastrowid))
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="department.create",
            resource_type="department",
            resource_id=created["id"],
            after=created,
        )
        return created

    def update(self, principal: Principal, department_id: int, changes: dict) -> dict:
        principal.require("departments.write")
        before = self.departments.require(department_id)
        allowed = {key: value for key, value in changes.items() if key in {"name", "manager", "phone", "is_active"} and value is not None}
        if not allowed:
            raise ValidationError("没有可更新的部门字段")
        if "name" in allowed:
            duplicate = self.departments.by_name(str(allowed["name"]).strip())
            if duplicate and duplicate["id"] != department_id:
                raise ConflictError("部门名称已存在")
        assignments = [f"{key}=?" for key in allowed]
        self.connection.execute(
            f"UPDATE departments SET {','.join(assignments)},updated_at=? WHERE id=?",
            (*allowed.values(), to_storage(self.clock.now()), department_id),
        )
        after = self.departments.require(department_id)
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="department.update",
            resource_type="department",
            resource_id=department_id,
            before=before,
            after=after,
        )
        return after

    def add_membership(self, principal: Principal, user_id: int, data: dict) -> dict:
        principal.require("departments.write")
        user = self.users.require(user_id)
        department = self.departments.require(data["department_id"])
        starts_at = from_storage(data["starts_at"])
        ends_at = from_storage(data.get("ends_at"))
        if starts_at is None:
            raise ValidationError("任职开始时间不能为空")
        if ends_at is not None and ends_at <= starts_at:
            raise ValidationError("任职结束时间必须晚于开始时间")
        overlap = self.connection.execute(
            "SELECT id FROM department_memberships WHERE user_id=? AND department_id=? "
            "AND starts_at<? AND (ends_at IS NULL OR ends_at>?) LIMIT 1",
            (user_id, data["department_id"], to_storage(ends_at) if ends_at else "9999-12-31T23:59:59+00:00", to_storage(starts_at)),
        ).fetchone()
        if overlap:
            raise ConflictError("该用户在此部门已有重叠任期")
        now = to_storage(self.clock.now())
        if data.get("is_primary"):
            self.connection.execute(
                "UPDATE department_memberships SET is_primary=0 WHERE user_id=? AND (ends_at IS NULL OR ends_at>?)",
                (user_id, now),
            )
        cursor = self.connection.execute(
            "INSERT INTO department_memberships(user_id,department_id,title,is_primary,starts_at,ends_at,created_at) VALUES(?,?,?,?,?,?,?)",
            (user_id, data["department_id"], data.get("title", ""), 1 if data.get("is_primary") else 0, to_storage(starts_at), to_storage(ends_at) if ends_at else None, now),
        )
        if data.get("is_primary"):
            self.connection.execute("UPDATE users SET department_id=?,updated_at=? WHERE id=?", (data["department_id"], now, user_id))
        membership = self.departments.membership(int(cursor.lastrowid))
        assert membership is not None
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="department.membership.create",
            resource_type="department_membership",
            resource_id=membership["id"],
            after=membership,
            metadata={"user": user["username"], "department": department["name"]},
        )
        return membership

    def end_membership(self, principal: Principal, membership_id: int, ends_at: str) -> dict:
        principal.require("departments.write")
        membership = self.departments.membership(membership_id)
        if membership is None:
            raise NotFoundError("部门任职记录不存在")
        end = from_storage(ends_at)
        start = from_storage(membership["starts_at"])
        if end is None or start is None or end <= start:
            raise ValidationError("任职结束时间无效")
        self.connection.execute("UPDATE department_memberships SET ends_at=?,is_primary=0 WHERE id=?", (to_storage(end), membership_id))
        if membership["is_primary"]:
            self.connection.execute("UPDATE users SET department_id=NULL,updated_at=? WHERE id=? AND department_id=?", (to_storage(self.clock.now()), membership["user_id"], membership["department_id"]))
        after = self.departments.membership(membership_id)
        assert after is not None
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="department.membership.end",
            resource_type="department_membership",
            resource_id=membership_id,
            before=membership,
            after=after,
        )
        return after
