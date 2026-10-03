from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories.base import Repository, row_dict, rows_dict


class UserRepository(Repository):
    table = "users"
    entity_name = "用户"

    def by_username(self, username: str) -> dict[str, Any] | None:
        return row_dict(self.connection.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())

    def list(self, *, status: str | None, department_id: int | None, limit: int, offset: int) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        if status:
            conditions.append("u.status=?")
            params.append(status)
        if department_id is not None:
            conditions.append("u.department_id=?")
            params.append(department_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])
        return rows_dict(self.connection.execute(
            "SELECT u.*,d.name AS department_name FROM users u LEFT JOIN departments d ON d.id=u.department_id"
            + where + " ORDER BY u.id LIMIT ? OFFSET ?", tuple(params)
        ).fetchall())

    def permissions(self, user_id: int) -> set[str]:
        rows = self.connection.execute(
            "SELECT DISTINCT p.code FROM permissions p "
            "JOIN role_permissions rp ON rp.permission_id=p.id "
            "JOIN user_roles ur ON ur.role_id=rp.role_id "
            "WHERE ur.user_id=?",
            (user_id,),
        ).fetchall()
        return {str(row[0]) for row in rows}

    def roles(self, user_id: int) -> list[dict]:
        return rows_dict(self.connection.execute(
            "SELECT r.* FROM roles r JOIN user_roles ur ON ur.role_id=r.id "
            "WHERE ur.user_id=? ORDER BY r.code", (user_id,)
        ).fetchall())


class RoleRepository(Repository):
    table = "roles"
    entity_name = "角色"

    def by_code(self, code: str) -> dict[str, Any] | None:
        return row_dict(self.connection.execute("SELECT * FROM roles WHERE code=?", (code,)).fetchone())

    def list(self) -> list[dict]:
        return rows_dict(self.connection.execute(
            "SELECT r.*,COUNT(DISTINCT rp.permission_id) AS permission_count, "
            "COUNT(DISTINCT ur.user_id) AS user_count FROM roles r "
            "LEFT JOIN role_permissions rp ON rp.role_id=r.id "
            "LEFT JOIN user_roles ur ON ur.role_id=r.id "
            "GROUP BY r.id ORDER BY r.code"
        ).fetchall())

    def permissions(self, role_id: int) -> list[dict]:
        return rows_dict(self.connection.execute(
            "SELECT p.* FROM permissions p JOIN role_permissions rp ON rp.permission_id=p.id "
            "WHERE rp.role_id=? ORDER BY p.code", (role_id,)
        ).fetchall())


class SessionRepository(Repository):
    table = "sessions"
    entity_name = "会话"

    def active_by_digest(self, digest: str) -> dict[str, Any] | None:
        return row_dict(self.connection.execute(
            "SELECT * FROM sessions WHERE token_digest=? AND revoked_at IS NULL", (digest,)
        ).fetchone())

    def revoke_user_sessions(self, user_id: int, revoked_at: str, reason: str) -> int:
        cursor = self.connection.execute(
            "UPDATE sessions SET revoked_at=?,revoke_reason=? WHERE user_id=? AND revoked_at IS NULL",
            (revoked_at, reason, user_id),
        )
        return cursor.rowcount
