from __future__ import annotations

import sqlite3

from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import Principal, hash_password, normalize_username
from app.repositories.identity import RoleRepository, SessionRepository, UserRepository
from app.services.audit import AuditContext, AuditService


class IdentityService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.connection = connection
        self.clock = clock or SystemClock()
        self.users = UserRepository(connection)
        self.roles = RoleRepository(connection)
        self.sessions = SessionRepository(connection)
        self.audit = AuditService(connection, self.clock)

    def create_user(self, principal: Principal, data: dict) -> dict:
        principal.require("users.write")
        username = normalize_username(data["username"])
        if self.users.by_username(username):
            raise ConflictError("用户名已存在")
        if data.get("department_id") is not None:
            if not self.connection.execute("SELECT 1 FROM departments WHERE id=? AND is_active=1", (data["department_id"],)).fetchone():
                raise NotFoundError("部门不存在或已停用")
        roles = self._resolve_roles(data.get("role_codes", []))
        now = to_storage(self.clock.now())
        cursor = self.connection.execute(
            "INSERT INTO users(username,password_hash,display_name,email,phone,department_id,status,"
            "password_changed_at,created_at,updated_at) VALUES(?,?,?,?,?,?,'active',?,?,?)",
            (username, hash_password(data["password"]), data["display_name"].strip(), data.get("email"), data.get("phone"), data.get("department_id"), now, now, now),
        )
        user_id = int(cursor.lastrowid)
        for role in roles:
            self.connection.execute(
                "INSERT INTO user_roles(user_id,role_id,assigned_by,assigned_at) VALUES(?,?,?,?)",
                (user_id, role["id"], principal.user_id, now),
            )
        created = self.users.require(user_id)
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="user.create",
            resource_type="user",
            resource_id=user_id,
            after={**created, "roles": [role["code"] for role in roles]},
        )
        return self.detail(user_id)

    def update_user(self, principal: Principal, user_id: int, changes: dict) -> dict:
        principal.require("users.write")
        before = self.users.require(user_id)
        allowed = {key: value for key, value in changes.items() if key in {"display_name", "email", "phone", "department_id", "status"}}
        if not allowed:
            raise ValidationError("没有可更新的用户字段")
        if allowed.get("department_id") is not None:
            if not self.connection.execute("SELECT 1 FROM departments WHERE id=? AND is_active=1", (allowed["department_id"],)).fetchone():
                raise NotFoundError("部门不存在或已停用")
        assignments = [f"{key}=?" for key in allowed]
        params = list(allowed.values())
        params.extend([to_storage(self.clock.now()), user_id])
        self.connection.execute(f"UPDATE users SET {','.join(assignments)},updated_at=? WHERE id=?", params)
        after = self.users.require(user_id)
        if before["status"] == "active" and after["status"] != "active":
            self.sessions.revoke_user_sessions(user_id, to_storage(self.clock.now()), "user_status_changed")
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="user.update",
            resource_type="user",
            resource_id=user_id,
            before=before,
            after=after,
        )
        return self.detail(user_id)

    def assign_roles(self, principal: Principal, user_id: int, role_codes: list[str]) -> dict:
        principal.require("roles.write")
        user = self.users.require(user_id)
        roles = self._resolve_roles(role_codes)
        before = [item["code"] for item in self.users.roles(user_id)]
        now = to_storage(self.clock.now())
        self.connection.execute("DELETE FROM user_roles WHERE user_id=?", (user_id,))
        for role in roles:
            self.connection.execute(
                "INSERT INTO user_roles(user_id,role_id,assigned_by,assigned_at) VALUES(?,?,?,?)",
                (user_id, role["id"], principal.user_id, now),
            )
        after = [role["code"] for role in roles]
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="user.roles.replace",
            resource_type="user",
            resource_id=user_id,
            before={"roles": before},
            after={"roles": after},
            metadata={"target": user["username"]},
        )
        return self.detail(user_id)

    def detail(self, user_id: int) -> dict:
        user = self.users.require(user_id)
        user.pop("password_hash", None)
        user["roles"] = self.users.roles(user_id)
        user["permissions"] = sorted(self.users.permissions(user_id))
        return user

    def create_role(self, principal: Principal, data: dict) -> dict:
        principal.require("roles.write")
        if self.roles.by_code(data["code"]):
            raise ConflictError("角色编码已存在")
        permission_ids = self._resolve_permissions(data.get("permission_codes", []))
        now = to_storage(self.clock.now())
        cursor = self.connection.execute(
            "INSERT INTO roles(code,name,description,is_system,created_at,updated_at) VALUES(?,?,?,0,?,?)",
            (data["code"], data["name"], data.get("description", ""), now, now),
        )
        role_id = int(cursor.lastrowid)
        for permission_id in permission_ids:
            self.connection.execute(
                "INSERT INTO role_permissions(role_id,permission_id,granted_at) VALUES(?,?,?)",
                (role_id, permission_id, now),
            )
        role = self.role_detail(role_id)
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="role.create",
            resource_type="role",
            resource_id=role_id,
            after=role,
        )
        return role

    def update_role(self, principal: Principal, role_id: int, data: dict) -> dict:
        principal.require("roles.write")
        before = self.role_detail(role_id)
        role = self.roles.require(role_id)
        updates = {key: value for key, value in data.items() if key in {"name", "description"} and value is not None}
        if updates:
            assignments = [f"{key}=?" for key in updates]
            self.connection.execute(
                f"UPDATE roles SET {','.join(assignments)},updated_at=? WHERE id=?",
                (*updates.values(), to_storage(self.clock.now()), role_id),
            )
        if data.get("permission_codes") is not None:
            permission_ids = self._resolve_permissions(data["permission_codes"])
            self.connection.execute("DELETE FROM role_permissions WHERE role_id=?", (role_id,))
            for permission_id in permission_ids:
                self.connection.execute(
                    "INSERT INTO role_permissions(role_id,permission_id,granted_at) VALUES(?,?,?)",
                    (role_id, permission_id, to_storage(self.clock.now())),
                )
        after = self.role_detail(role_id)
        self.audit.record(
            AuditContext(principal.user_id, principal.display_name),
            action="role.update",
            resource_type="role",
            resource_id=role_id,
            before=before,
            after=after,
            metadata={"system_role": bool(role["is_system"])},
        )
        return after

    def role_detail(self, role_id: int) -> dict:
        role = self.roles.require(role_id)
        role["permissions"] = self.roles.permissions(role_id)
        return role

    def _resolve_roles(self, codes: list[str]) -> list[dict]:
        roles: list[dict] = []
        for code in dict.fromkeys(codes):
            role = self.roles.by_code(code)
            if role is None:
                raise NotFoundError(f"角色不存在：{code}")
            roles.append(role)
        return roles

    def _resolve_permissions(self, codes: list[str]) -> list[int]:
        result: list[int] = []
        for code in dict.fromkeys(codes):
            row = self.connection.execute("SELECT id FROM permissions WHERE code=?", (code,)).fetchone()
            if row is None:
                raise NotFoundError(f"权限不存在：{code}")
            result.append(int(row[0]))
        return result
