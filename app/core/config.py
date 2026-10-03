from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ValidationError


def _positive_integer(name: str, default: int, *, minimum: int = 1, maximum: int = 100_000) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValidationError(f"配置 {name} 必须是整数") from exc
    if not minimum <= value <= maximum:
        raise ValidationError(f"配置 {name} 必须在 {minimum} 到 {maximum} 之间")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    database_path: Path
    session_ttl_minutes: int
    login_failure_limit: int
    login_lock_minutes: int
    default_page_size: int
    audit_retention_days: int
    job_lease_seconds: int

    @classmethod
    def load(cls) -> "Settings":
        default_database = Path(__file__).resolve().parents[2] / "data" / "deep-travel.db"
        database = Path(os.getenv("DEEP_TRAVEL_DATABASE_PATH", str(default_database))).expanduser().resolve()
        if database.suffix.casefold() not in {".db", ".sqlite", ".sqlite3"}:
            raise ValidationError("数据库文件必须使用 .db、.sqlite 或 .sqlite3 后缀")
        return cls(
            database_path=database,
            session_ttl_minutes=_positive_integer("DEEP_TRAVEL_SESSION_TTL_MINUTES", 480, maximum=43_200),
            login_failure_limit=_positive_integer("DEEP_TRAVEL_LOGIN_FAILURE_LIMIT", 5, maximum=100),
            login_lock_minutes=_positive_integer("DEEP_TRAVEL_LOGIN_LOCK_MINUTES", 30, maximum=10_080),
            default_page_size=_positive_integer("DEEP_TRAVEL_DEFAULT_PAGE_SIZE", 20, maximum=100),
            audit_retention_days=_positive_integer("DEEP_TRAVEL_AUDIT_RETENTION_DAYS", 365, maximum=3650),
            job_lease_seconds=_positive_integer("DEEP_TRAVEL_JOB_LEASE_SECONDS", 60, maximum=3600),
        )

    def public_view(self) -> dict:
        return {
            "database_path": str(self.database_path),
            "session_ttl_minutes": self.session_ttl_minutes,
            "login_failure_limit": self.login_failure_limit,
            "login_lock_minutes": self.login_lock_minutes,
            "default_page_size": self.default_page_size,
            "audit_retention_days": self.audit_retention_days,
            "job_lease_seconds": self.job_lease_seconds,
        }
