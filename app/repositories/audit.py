from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.repositories.base import rows_dict


SENSITIVE_KEYS = {"password", "password_hash", "token", "token_digest", "secret", "authorization"}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items() if key.casefold() not in SENSITIVE_KEYS}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class AuditRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def append(
        self,
        *,
        actor_user_id: int | None,
        actor_name: str,
        action: str,
        resource_type: str,
        resource_id: str | int | None,
        outcome: str,
        before: dict | None,
        after: dict | None,
        metadata: dict | None,
        correlation_id: str | None,
        created_at: str,
    ) -> int:
        cursor = self.connection.execute(
            "INSERT INTO audit_events(actor_user_id,actor_name,action,resource_type,resource_id,outcome,"
            "before_json,after_json,metadata_json,correlation_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                actor_user_id,
                actor_name,
                action,
                resource_type,
                str(resource_id) if resource_id is not None else None,
                outcome,
                json.dumps(redact(before), ensure_ascii=False, sort_keys=True) if before is not None else None,
                json.dumps(redact(after), ensure_ascii=False, sort_keys=True) if after is not None else None,
                json.dumps(redact(metadata or {}), ensure_ascii=False, sort_keys=True),
                correlation_id,
                created_at,
            ),
        )
        return int(cursor.lastrowid)

    def list(
        self,
        *,
        actor_user_id: int | None,
        resource_type: str | None,
        action: str | None,
        outcome: str | None,
        limit: int,
        offset: int,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("actor_user_id", actor_user_id),
            ("resource_type", resource_type),
            ("action", action),
            ("outcome", outcome),
        ):
            if value is not None:
                conditions.append(f"{column}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.extend([limit, offset])
        return rows_dict(self.connection.execute(
            "SELECT * FROM audit_events" + where + " ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params),
        ).fetchall())
