from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, TypeVar

from app.core.clock import Clock, SystemClock, to_storage
from app.core.errors import ConflictError
from app.core.security import request_fingerprint

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class StoredResponse:
    body: dict
    status_code: int
    replayed: bool


class IdempotencyService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.connection = connection
        self.clock = clock or SystemClock()

    def lookup(self, scope: str, key: str, payload: dict) -> StoredResponse | None:
        row = self.connection.execute(
            "SELECT * FROM idempotency_records WHERE scope=? AND idempotency_key=?", (scope, key)
        ).fetchone()
        if row is None:
            return None
        fingerprint = request_fingerprint(payload)
        if row["request_hash"] != fingerprint:
            raise ConflictError("同一幂等键不能用于不同请求")
        return StoredResponse(json.loads(row["response_json"]), int(row["status_code"]), True)

    def save(self, scope: str, key: str, payload: dict, body: dict, status_code: int) -> StoredResponse:
        fingerprint = request_fingerprint(payload)
        try:
            self.connection.execute(
                "INSERT INTO idempotency_records(scope,idempotency_key,request_hash,response_json,status_code,created_at) VALUES(?,?,?,?,?,?)",
                (scope, key, fingerprint, json.dumps(body, ensure_ascii=False, sort_keys=True), status_code, to_storage(self.clock.now())),
            )
        except sqlite3.IntegrityError:
            existing = self.lookup(scope, key, payload)
            if existing is None:
                raise
            return existing
        return StoredResponse(body, status_code, False)

    def execute(self, scope: str, key: str, payload: dict, operation: Callable[[], tuple[dict, int]]) -> StoredResponse:
        existing = self.lookup(scope, key, payload)
        if existing is not None:
            return existing
        body, status_code = operation()
        return self.save(scope, key, payload, body, status_code)
