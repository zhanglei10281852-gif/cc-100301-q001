from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from app.core.errors import NotFoundError


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_dict(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


class Repository:
    table: str
    entity_name: str

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, entity_id: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            f"SELECT * FROM {self.table} WHERE id=?", (entity_id,)
        ).fetchone()
        return row_dict(row)

    def require(self, entity_id: int) -> dict[str, Any]:
        item = self.get(entity_id)
        if item is None:
            raise NotFoundError(f"{self.entity_name}不存在")
        return item

    def count(self, where: str = "", params: tuple = ()) -> int:
        query = f"SELECT COUNT(*) FROM {self.table}"
        if where:
            query += " WHERE " + where
        return int(self.connection.execute(query, params).fetchone()[0])

    def delete(self, entity_id: int) -> bool:
        cursor = self.connection.execute(f"DELETE FROM {self.table} WHERE id=?", (entity_id,))
        return cursor.rowcount > 0
