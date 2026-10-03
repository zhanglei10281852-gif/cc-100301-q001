from __future__ import annotations

from typing import Any

from app.repositories.base import Repository, row_dict, rows_dict


class DepartmentRepository(Repository):
    table = "departments"
    entity_name = "协作团队"

    def by_name(self, name: str) -> dict[str, Any] | None:
        return row_dict(self.connection.execute("SELECT * FROM departments WHERE name=?", (name,)).fetchone())

    def list(self, *, active_only: bool, limit: int, offset: int) -> list[dict]:
        where = " WHERE d.is_active=1" if active_only else ""
        return rows_dict(self.connection.execute(
            "SELECT d.*,COUNT(DISTINCT u.id) AS user_count FROM departments d "
            "LEFT JOIN users u ON u.department_id=d.id" + where +
            " GROUP BY d.id ORDER BY d.name LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall())

    def active_memberships(self, department_id: int, moment: str) -> list[dict]:
        return rows_dict(self.connection.execute(
            "SELECT m.*,u.username,u.display_name,u.status FROM department_memberships m "
            "JOIN users u ON u.id=m.user_id WHERE m.department_id=? "
            "AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) ORDER BY m.is_primary DESC,u.display_name",
            (department_id, moment, moment),
        ).fetchall())

    def membership(self, membership_id: int) -> dict[str, Any] | None:
        return row_dict(self.connection.execute(
            "SELECT m.*,u.username,u.display_name,d.name AS department_name FROM department_memberships m "
            "JOIN users u ON u.id=m.user_id JOIN departments d ON d.id=m.department_id WHERE m.id=?",
            (membership_id,),
        ).fetchone())

    def user_memberships(self, user_id: int, moment: str) -> list[dict]:
        return rows_dict(self.connection.execute(
            "SELECT m.*,d.name AS department_name FROM department_memberships m "
            "JOIN departments d ON d.id=m.department_id WHERE m.user_id=? "
            "AND m.starts_at<=? AND (m.ends_at IS NULL OR m.ends_at>?) ORDER BY m.is_primary DESC,d.name",
            (user_id, moment, moment),
        ).fetchall())

