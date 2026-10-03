from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from app.core.clock import Clock, SystemClock, to_storage
from app.core.config import Settings
from app.core.security import Principal


@dataclass(frozen=True, slots=True)
class BackupManifest:
    schema_version: int
    generated_at: str
    tables: dict[str, int]
    checksums: dict[str, str]

    def as_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "tables": self.tables,
            "checksums": self.checksums,
        }


class MaintenanceService:
    EXPORT_TABLES = (
        "departments",
        "users",
        "roles",
        "permissions",
        "role_permissions",
        "user_roles",
        "department_memberships",
        "audit_events",
        "background_jobs",
        "travel_products",
        "pilot_sites",
        "evidence_documents",
        "public_feedback",
        "pilot_protocols",
        "pilot_quotas",
        "pilot_sessions",
        "pilot_observations",
        "pilot_interventions",
    )

    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.connection = connection
        self.clock = clock or SystemClock()
        self.settings = Settings.load()

    def manifest(self, principal: Principal) -> dict:
        principal.require("audit.read")
        tables: dict[str, int] = {}
        checksums: dict[str, str] = {}
        for table in self.EXPORT_TABLES:
            rows = [dict(row) for row in self.connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]
            tables[table] = len(rows)
            payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            checksums[table] = hashlib.sha256(payload.encode()).hexdigest()
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        return BackupManifest(version, to_storage(self.clock.now()), tables, checksums).as_dict()

    def prune_expired_sessions(self, principal: Principal) -> dict:
        principal.require("jobs.run")
        now = to_storage(self.clock.now())
        cursor = self.connection.execute(
            "DELETE FROM sessions WHERE expires_at<? OR (revoked_at IS NOT NULL AND revoked_at<?)",
            (now, to_storage(self.clock.now() - timedelta(days=30))),
        )
        return {"deleted_sessions": cursor.rowcount, "finished_at": now}

    def prune_audit(self, principal: Principal) -> dict:
        principal.require("jobs.run")
        cutoff = to_storage(self.clock.now() - timedelta(days=self.settings.audit_retention_days))
        cursor = self.connection.execute("DELETE FROM audit_events WHERE created_at<?", (cutoff,))
        return {"deleted_events": cursor.rowcount, "cutoff": cutoff}

    def database_diagnostics(self, principal: Principal) -> dict:
        principal.require("audit.read")
        integrity = str(self.connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_errors = [dict(row) for row in self.connection.execute("PRAGMA foreign_key_check").fetchall()]
        page_count = int(self.connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(self.connection.execute("PRAGMA page_size").fetchone()[0])
        return {
            "integrity": integrity,
            "foreign_key_errors": foreign_key_errors,
            "database_bytes": page_count * page_size,
            "journal_mode": str(self.connection.execute("PRAGMA journal_mode").fetchone()[0]),
            "generated_at": to_storage(self.clock.now()),
        }
