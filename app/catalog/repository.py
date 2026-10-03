from __future__ import annotations

import json
import sqlite3
from typing import Any


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class CatalogRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def product_by_code(self, code: str) -> dict[str, Any] | None:
        return _dict(self.connection.execute("SELECT * FROM travel_products WHERE code=?", (code,)).fetchone())

    def product_by_id(self, product_id: int) -> dict[str, Any] | None:
        return _dict(self.connection.execute("SELECT * FROM travel_products WHERE id=?", (product_id,)).fetchone())

    def create_product(self, data: dict, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO travel_products(code,name,organization,origin_country,category,intended_use,risk_level,regulatory_status,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,?)",
            (data["code"], data["name"], data["organization"], data["origin_country"], data["category"], data["intended_use"], data["risk_level"], data["regulatory_status"], now, now),
        )
        return self.product_by_id(int(cursor.lastrowid)) or {}

    def update_product(self, product_id: int, changes: dict, now: str) -> dict[str, Any]:
        values = {key: value for key, value in changes.items() if value is not None}
        if "active" in values:
            values["active"] = 1 if values["active"] else 0
        assignments = [f"{key}=?" for key in values]
        self.connection.execute(
            f"UPDATE travel_products SET {','.join(assignments)},updated_at=? WHERE id=?",
            (*values.values(), now, product_id),
        )
        return self.product_by_id(product_id) or {}

    def list_products(self, *, category: str | None, status: str | None, active_only: bool, limit: int) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("p.category=?")
            params.append(category)
        if status:
            clauses.append("p.regulatory_status=?")
            params.append(status)
        if active_only:
            clauses.append("p.active=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self.connection.execute(
            "SELECT p.*,COUNT(DISTINCT e.id) AS evidence_count,COUNT(DISTINCT f.id) AS feedback_count "
            "FROM travel_products p LEFT JOIN evidence_documents e ON e.product_id=p.id "
            "LEFT JOIN public_feedback f ON f.product_id=p.id" + where +
            " GROUP BY p.id ORDER BY p.updated_at DESC,p.id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]

    def site_by_code(self, code: str) -> dict[str, Any] | None:
        return _dict(self.connection.execute("SELECT * FROM pilot_sites WHERE code=?", (code,)).fetchone())

    def site_by_id(self, site_id: int) -> dict[str, Any] | None:
        return _dict(self.connection.execute("SELECT * FROM pilot_sites WHERE id=?", (site_id,)).fetchone())

    def create_site(self, data: dict, now: str) -> dict[str, Any]:
        cursor = self.connection.execute(
            "INSERT INTO pilot_sites(code,name,site_type,region,capabilities_json,max_concurrent,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'active',?,?)",
            (data["code"], data["name"], data["site_type"], data["region"], json.dumps(sorted(set(data["capabilities"])), ensure_ascii=False), data["max_concurrent"], now, now),
        )
        return self.site_by_id(int(cursor.lastrowid)) or {}

    def update_site(self, site_id: int, changes: dict, now: str) -> dict[str, Any]:
        values = {key: value for key, value in changes.items() if value is not None}
        if "capabilities" in values:
            values["capabilities_json"] = json.dumps(sorted(set(values.pop("capabilities"))), ensure_ascii=False)
        assignments = [f"{key}=?" for key in values]
        self.connection.execute(
            f"UPDATE pilot_sites SET {','.join(assignments)},updated_at=? WHERE id=?",
            (*values.values(), now, site_id),
        )
        return self.site_by_id(site_id) or {}

    def list_sites(self, *, status: str | None, site_type: str | None, capability: str | None) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if site_type:
            clauses.append("site_type=?")
            params.append(site_type)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = [dict(row) for row in self.connection.execute("SELECT * FROM pilot_sites" + where + " ORDER BY region,name", tuple(params)).fetchall()]
        if capability:
            rows = [row for row in rows if capability in json.loads(row["capabilities_json"])]
        return rows

    def evidence_duplicate(self, product_id: int, evidence_type: str, version: str, digest: str) -> dict | None:
        return _dict(self.connection.execute(
            "SELECT * FROM evidence_documents WHERE product_id=? AND evidence_type=? AND version=? AND content_digest=?",
            (product_id, evidence_type, version, digest),
        ).fetchone())

    def evidence_by_id(self, evidence_id: int) -> dict | None:
        return _dict(self.connection.execute("SELECT * FROM evidence_documents WHERE id=?", (evidence_id,)).fetchone())

    def create_evidence(self, product_id: int, data: dict, now: str) -> dict:
        cursor = self.connection.execute(
            "INSERT INTO evidence_documents(product_id,evidence_type,title,source_name,source_region,version,content_digest,summary_json,status,submitted_by,submitted_at) VALUES(?,?,?,?,?,?,?,?, 'submitted',?,?)",
            (product_id, data["evidence_type"], data["title"], data["source_name"], data["source_region"], data["version"], data["content_digest"], json.dumps(data["summary"], ensure_ascii=False, sort_keys=True), data["submitted_by"], now),
        )
        return self.evidence_by_id(int(cursor.lastrowid)) or {}

    def review_evidence(self, evidence_id: int, reviewer: str, decision: str, now: str) -> dict:
        self.connection.execute(
            "UPDATE evidence_documents SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?",
            (decision, reviewer, now, evidence_id),
        )
        return self.evidence_by_id(evidence_id) or {}

    def list_evidence(self, *, product_id: int | None, status: str | None) -> list[dict]:
        clauses: list[str] = []
        params: list[Any] = []
        if product_id is not None:
            clauses.append("e.product_id=?")
            params.append(product_id)
        if status:
            clauses.append("e.status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [dict(row) for row in self.connection.execute(
            "SELECT e.*,p.code AS product_code,p.name AS product_name FROM evidence_documents e JOIN travel_products p ON p.id=e.product_id" + where + " ORDER BY e.submitted_at DESC,e.id DESC",
            tuple(params),
        ).fetchall()]

    def feedback_duplicate(self, site_id: int, session_reference: str, audience_type: str, contact_digest: str) -> dict | None:
        return _dict(self.connection.execute(
            "SELECT * FROM public_feedback WHERE site_id=? AND session_reference=? AND audience_type=? AND contact_digest=?",
            (site_id, session_reference, audience_type, contact_digest),
        ).fetchone())

    def create_feedback(self, product_id: int, site_id: int, data: dict, now: str) -> dict:
        cursor = self.connection.execute(
            "INSERT INTO public_feedback(product_id,site_id,session_reference,audience_type,rating,tags_json,comment,contact_digest,consent_to_follow_up,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (product_id, site_id, data["session_reference"], data["audience_type"], data["rating"], json.dumps(sorted(set(data["tags"])), ensure_ascii=False), data["comment"], data["contact_digest"], 1 if data["consent_to_follow_up"] else 0, now),
        )
        return dict(self.connection.execute("SELECT * FROM public_feedback WHERE id=?", (cursor.lastrowid,)).fetchone())

    def feedback_summary(self, product_id: int | None = None) -> list[dict]:
        where = " WHERE p.id=?" if product_id is not None else ""
        params = (product_id,) if product_id is not None else ()
        rows = self.connection.execute(
            "SELECT p.id AS product_id,p.code AS product_code,p.name AS product_name,COUNT(f.id) AS feedback_count,"
            "ROUND(AVG(f.rating),2) AS average_rating,SUM(CASE WHEN f.consent_to_follow_up=1 THEN 1 ELSE 0 END) AS follow_up_count "
            "FROM travel_products p LEFT JOIN public_feedback f ON f.product_id=p.id" + where + " GROUP BY p.id ORDER BY feedback_count DESC,p.code",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

