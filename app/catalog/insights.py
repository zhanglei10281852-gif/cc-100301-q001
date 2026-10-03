from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.core.errors import NotFoundError, ValidationError


@dataclass(frozen=True, slots=True)
class ReadinessRule:
    risk_level: str
    required_types: tuple[str, ...]
    minimum_accepted: int
    minimum_feedback: int


RULES = {
    "low": ReadinessRule("low", ("性能", "游览"), 1, 1),
    "medium": ReadinessRule("medium", ("性能", "安全", "游览"), 2, 2),
    "high": ReadinessRule("high", ("线路", "性能", "安全", "合规"), 4, 3),
}


class CatalogInsights:
    """提供不改变业务状态的目录质量与运营占用分析。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def product_readiness(self, product_code: str) -> dict[str, Any]:
        product = self.connection.execute(
            "SELECT * FROM travel_products WHERE code=?",
            (product_code,),
        ).fetchone()
        if product is None:
            raise NotFoundError("深度旅行产品不存在")
        accepted = [dict(row) for row in self.connection.execute(
            "SELECT evidence_type,source_region,version,reviewed_at FROM evidence_documents "
            "WHERE product_id=? AND status='accepted' ORDER BY evidence_type,reviewed_at,id",
            (product["id"],),
        ).fetchall()]
        feedback = self.connection.execute(
            "SELECT COUNT(*) AS amount,AVG(rating) AS average_rating,"
            "SUM(CASE WHEN consent_to_follow_up=1 THEN 1 ELSE 0 END) AS follow_up "
            "FROM public_feedback WHERE product_id=?",
            (product["id"],),
        ).fetchone()
        rule = RULES[str(product["risk_level"])]
        accepted_types = {str(item["evidence_type"]) for item in accepted}
        missing_types = [item for item in rule.required_types if item not in accepted_types]
        evidence_ready = len(accepted) >= rule.minimum_accepted and not missing_types
        feedback_amount = int(feedback["amount"] or 0)
        feedback_ready = feedback_amount >= rule.minimum_feedback
        blockers: list[str] = []
        if not product["active"]:
            blockers.append("产品已停用")
        if product["regulatory_status"] == "暂停":
            blockers.append("产品处于暂停状态")
        if missing_types:
            blockers.append("缺少已接受证据：" + "、".join(missing_types))
        if len(accepted) < rule.minimum_accepted:
            blockers.append(f"已接受证据少于 {rule.minimum_accepted} 份")
        if not feedback_ready:
            blockers.append(f"有效游客反馈少于 {rule.minimum_feedback} 条")
        ready = bool(product["active"]) and product["regulatory_status"] != "暂停" and evidence_ready and feedback_ready
        return {
            "product_code": product["code"],
            "product_name": product["name"],
            "risk_level": product["risk_level"],
            "regulatory_status": product["regulatory_status"],
            "accepted_evidence": len(accepted),
            "accepted_types": sorted(accepted_types),
            "missing_types": missing_types,
            "feedback_count": feedback_amount,
            "average_rating": round(float(feedback["average_rating"]), 2) if feedback["average_rating"] is not None else None,
            "follow_up_count": int(feedback["follow_up"] or 0),
            "ready_for_expansion": ready,
            "blockers": blockers,
        }

    def feedback_tags(self, product_code: str, limit: int = 20) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ValidationError("标签数量必须在 1 到 100 之间")
        product = self.connection.execute(
            "SELECT id,name FROM travel_products WHERE code=?",
            (product_code,),
        ).fetchone()
        if product is None:
            raise NotFoundError("深度旅行产品不存在")
        counter: Counter[str] = Counter()
        audience_counter: Counter[str] = Counter()
        ratings: Counter[int] = Counter()
        for row in self.connection.execute(
            "SELECT audience_type,rating,tags_json FROM public_feedback WHERE product_id=? ORDER BY id",
            (product["id"],),
        ).fetchall():
            audience_counter[str(row["audience_type"])] += 1
            ratings[int(row["rating"])] += 1
            try:
                tags = json.loads(row["tags_json"])
            except (TypeError, ValueError):
                tags = []
            for raw in tags:
                tag = str(raw).strip()
                if tag:
                    counter[tag] += 1
        return {
            "product_code": product_code,
            "product_name": product["name"],
            "tags": [{"tag": tag, "amount": amount} for tag, amount in counter.most_common(limit)],
            "audiences": dict(sorted(audience_counter.items())),
            "ratings": {str(score): ratings.get(score, 0) for score in range(1, 6)},
        }

    def site_utilization(self, site_code: str) -> dict[str, Any]:
        site = self.connection.execute(
            "SELECT * FROM pilot_sites WHERE code=?",
            (site_code,),
        ).fetchone()
        if site is None:
            raise NotFoundError("目的地节点不存在")
        running = int(self.connection.execute(
            "SELECT COUNT(*) FROM pilot_sessions WHERE lease_owner=? AND status IN ('running','cancel_requested')",
            (site_code,),
        ).fetchone()[0])
        completed = int(self.connection.execute(
            "SELECT COUNT(*) FROM pilot_observations WHERE created_by=?",
            (site_code,),
        ).fetchone()[0])
        failed = int(self.connection.execute(
            "SELECT COUNT(*) FROM pilot_sessions WHERE lease_owner=? AND status='failed'",
            (site_code,),
        ).fetchone()[0])
        capacity = int(site["max_concurrent"])
        available = max(0, capacity - running) if site["status"] == "active" else 0
        return {
            "site_code": site["code"],
            "site_name": site["name"],
            "status": site["status"],
            "capabilities": json.loads(site["capabilities_json"]),
            "max_concurrent": capacity,
            "active_sessions": running,
            "available_slots": available,
            "completed_observations": completed,
            "failed_sessions": failed,
            "over_capacity": running > capacity,
        }

    def portfolio_summary(self) -> dict[str, Any]:
        products = [dict(row) for row in self.connection.execute(
            "SELECT code,category,risk_level,regulatory_status,active FROM travel_products ORDER BY code"
        ).fetchall()]
        category_counts = Counter(str(item["category"]) for item in products if item["active"])
        risk_counts = Counter(str(item["risk_level"]) for item in products if item["active"])
        status_counts = Counter(str(item["regulatory_status"]) for item in products)
        pending_evidence = int(self.connection.execute(
            "SELECT COUNT(*) FROM evidence_documents WHERE status='submitted'"
        ).fetchone()[0])
        sites = [dict(row) for row in self.connection.execute(
            "SELECT code,status,max_concurrent FROM pilot_sites ORDER BY code"
        ).fetchall()]
        total_capacity = sum(int(item["max_concurrent"]) for item in sites if item["status"] == "active")
        return {
            "products": len(products),
            "active_products": sum(1 for item in products if item["active"]),
            "categories": dict(sorted(category_counts.items())),
            "risk_levels": dict(sorted(risk_counts.items())),
            "regulatory_states": dict(sorted(status_counts.items())),
            "pending_evidence": pending_evidence,
            "sites": len(sites),
            "active_site_capacity": total_capacity,
        }

