from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from app.core.clock import Clock, SystemClock, to_storage
from app.core.security import Principal


@dataclass(frozen=True, slots=True)
class MetricWindow:
    days: int = 30


class MetricsService:
    def __init__(self, connection: sqlite3.Connection, clock: Clock | None = None) -> None:
        self.connection = connection
        self.clock = clock or SystemClock()

    def overview(self, principal: Principal, window: MetricWindow) -> dict:
        principal.require("catalog.read")
        since = to_storage(self.clock.now() - timedelta(days=window.days))
        products = int(self.connection.execute("SELECT COUNT(*) FROM travel_products WHERE active=1").fetchone()[0])
        sites = int(self.connection.execute("SELECT COUNT(*) FROM pilot_sites WHERE status='active'").fetchone()[0])
        evidence = int(self.connection.execute("SELECT COUNT(*) FROM evidence_documents WHERE submitted_at>=?", (since,)).fetchone()[0])
        feedback = int(self.connection.execute("SELECT COUNT(*) FROM public_feedback WHERE created_at>=?", (since,)).fetchone()[0])
        sessions = {str(row["status"]): int(row["amount"]) for row in self.connection.execute(
            "SELECT status,COUNT(*) AS amount FROM pilot_sessions GROUP BY status"
        ).fetchall()}
        ratings = [dict(row) for row in self.connection.execute(
            "SELECT p.code,p.name,COUNT(f.id) AS amount,ROUND(AVG(f.rating),2) AS average_rating "
            "FROM travel_products p LEFT JOIN public_feedback f ON f.product_id=p.id "
            "GROUP BY p.id ORDER BY amount DESC,p.code LIMIT 20"
        ).fetchall()]
        return {
            "window_days": window.days,
            "active_products": products,
            "active_sites": sites,
            "new_evidence": evidence,
            "new_feedback": feedback,
            "pilot_sessions": sessions,
            "product_ratings": ratings,
            "generated_at": to_storage(self.clock.now()),
        }

