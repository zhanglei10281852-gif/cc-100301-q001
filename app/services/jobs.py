from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

from app.core.clock import Clock, SystemClock, from_storage, to_storage
from app.core.errors import ConflictError, NotFoundError


class JobService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.connection = connection
        self.clock = clock or SystemClock()

    def enqueue(self, job_type: str, deduplication_key: str, payload: dict, *, delay_seconds: int = 0) -> dict:
        now = self.clock.now()
        try:
            cursor = self.connection.execute(
                "INSERT INTO background_jobs(job_type,deduplication_key,payload_json,status,available_at,created_at,updated_at) "
                "VALUES(?,?,?,'pending',?,?,?)",
                (job_type, deduplication_key, json.dumps(payload, ensure_ascii=False, sort_keys=True), to_storage(now + timedelta(seconds=delay_seconds)), to_storage(now), to_storage(now)),
            )
        except sqlite3.IntegrityError as exc:
            row = self.connection.execute("SELECT * FROM background_jobs WHERE deduplication_key=?", (deduplication_key,)).fetchone()
            if row:
                return dict(row)
            raise ConflictError("后台任务去重键冲突") from exc
        return dict(self.connection.execute("SELECT * FROM background_jobs WHERE id=?", (cursor.lastrowid,)).fetchone())

    def claim(self, worker: str, *, lease_seconds: int = 60) -> dict | None:
        now = self.clock.now()
        stale = to_storage(now - timedelta(seconds=lease_seconds))
        self.connection.execute(
            "UPDATE background_jobs SET status='pending',locked_at=NULL,locked_by=NULL,updated_at=? "
            "WHERE status='running' AND locked_at<?",
            (to_storage(now), stale),
        )
        row = self.connection.execute(
            "SELECT * FROM background_jobs WHERE status='pending' AND available_at<=? "
            "ORDER BY available_at,id LIMIT 1", (to_storage(now),)
        ).fetchone()
        if row is None:
            return None
        cursor = self.connection.execute(
            "UPDATE background_jobs SET status='running',attempts=attempts+1,locked_at=?,locked_by=?,updated_at=? "
            "WHERE id=? AND status='pending'",
            (to_storage(now), worker, to_storage(now), row["id"]),
        )
        if cursor.rowcount != 1:
            return None
        return dict(self.connection.execute("SELECT * FROM background_jobs WHERE id=?", (row["id"],)).fetchone())

    def complete(self, job_id: int, worker: str, result: dict) -> dict:
        now = to_storage(self.clock.now())
        cursor = self.connection.execute(
            "UPDATE background_jobs SET status='completed',result_json=?,locked_at=NULL,locked_by=NULL,updated_at=? "
            "WHERE id=? AND status='running' AND locked_by=?",
            (json.dumps(result, ensure_ascii=False, sort_keys=True), now, job_id, worker),
        )
        if cursor.rowcount != 1:
            raise NotFoundError("没有可由当前执行者完成的任务")
        return dict(self.connection.execute("SELECT * FROM background_jobs WHERE id=?", (job_id,)).fetchone())

    def fail(self, job_id: int, worker: str, message: str, *, retry_seconds: int | None = None) -> dict:
        now = self.clock.now()
        if retry_seconds is None:
            status = "failed"
            available_at = to_storage(now)
        else:
            status = "pending"
            available_at = to_storage(now + timedelta(seconds=retry_seconds))
        cursor = self.connection.execute(
            "UPDATE background_jobs SET status=?,error_message=?,available_at=?,locked_at=NULL,locked_by=NULL,updated_at=? "
            "WHERE id=? AND status='running' AND locked_by=?",
            (status, message[:1000], available_at, to_storage(now), job_id, worker),
        )
        if cursor.rowcount != 1:
            raise NotFoundError("没有可由当前执行者处理的任务")
        return dict(self.connection.execute("SELECT * FROM background_jobs WHERE id=?", (job_id,)).fetchone())
