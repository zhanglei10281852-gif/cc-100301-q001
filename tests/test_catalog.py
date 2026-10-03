from __future__ import annotations


PRODUCT = {
    "code": "purple-pottery-slow-tour",
    "name": "建水紫陶慢游体验",
    "organization": "建水社区旅行合作社",
    "origin_country": "中国",
    "category": "非遗工坊",
    "intended_use": "组织游客分时进入紫陶工坊完成制作体验，并保留社区接待和材料使用记录",
    "risk_level": "medium",
    "regulatory_status": "试运营",
}


SITE = {
    "code": "jianshui-workshop-a",
    "name": "建水紫陶社区工坊",
    "site_type": "社区合作点",
    "region": "云南建水",
    "capabilities": ["pottery-making", "slow-tour"],
    "max_concurrent": 3,
}


def test_product_site_evidence_and_feedback_flow(client):
    product = client.post("/api/catalog/products", json=PRODUCT)
    assert product.status_code == 201, product.text
    site = client.post("/api/catalog/sites", json=SITE)
    assert site.status_code == 201, site.text
    evidence = client.post("/api/catalog/evidence", json={
        "product_code": "purple-pottery-slow-tour",
        "evidence_type": "运力",
        "title": "假期工坊分时接待摘要",
        "source_name": "县域接待协调组",
        "source_region": "中国",
        "version": "2026.09",
        "content_digest": "a" * 64,
        "summary": {"sessions": 24, "metric": "on_time_rate"},
        "submitted_by": "evidence-owner",
    })
    assert evidence.status_code == 201, evidence.text
    reviewed = client.post(f"/api/catalog/evidence/{evidence.json()['id']}/review", json={"reviewer": "reviewer-a", "decision": "accepted", "note": "来源和版本可追溯"})
    assert reviewed.status_code == 200
    feedback = client.post("/api/catalog/feedback", json={
        "product_code": "purple-pottery-slow-tour",
        "site_code": "jianshui-workshop-a",
        "session_reference": "session-001",
        "audience_type": "带队人员",
        "rating": 4,
        "tags": ["节奏舒适", "讲解清楚"],
        "comment": "希望保留作品烧制进度和领取时间",
        "contact_digest": "contact-a",
        "consent_to_follow_up": True,
    })
    assert feedback.status_code == 201, feedback.text
    summary = client.get("/api/catalog/feedback/summary?product_code=purple-pottery-slow-tour")
    assert summary.status_code == 200
    assert summary.json()["items"][0]["feedback_count"] == 1
    readiness = client.get("/api/catalog/insights/products/purple-pottery-slow-tour/readiness")
    assert readiness.status_code == 200
    assert readiness.json()["accepted_evidence"] == 1
    tags = client.get("/api/catalog/insights/products/purple-pottery-slow-tour/feedback-tags")
    assert tags.status_code == 200
    assert tags.json()["tags"][0]["tag"] in {"节奏舒适", "讲解清楚"}
    utilization = client.get("/api/catalog/insights/sites/jianshui-workshop-a/utilization")
    assert utilization.status_code == 200
    assert utilization.json()["available_slots"] == 3


def test_evidence_and_feedback_are_idempotent(client):
    client.post("/api/catalog/products", json=PRODUCT)
    client.post("/api/catalog/sites", json=SITE)
    evidence_payload = {
        "product_code": "purple-pottery-slow-tour",
        "evidence_type": "安全",
        "title": "安全观察摘要",
        "source_name": "社区接待观察组",
        "source_region": "云南",
        "version": "v1",
        "content_digest": "b" * 64,
        "summary": {},
        "submitted_by": "owner-a",
    }
    first = client.post("/api/catalog/evidence", json=evidence_payload).json()
    second = client.post("/api/catalog/evidence", json=evidence_payload).json()
    assert first["id"] == second["id"]
    feedback_payload = {
        "product_code": "purple-pottery-slow-tour",
        "site_code": "jianshui-workshop-a",
        "session_reference": "same-session",
        "audience_type": "游客",
        "rating": 5,
        "tags": ["慢游"],
        "comment": "工坊游览顺畅",
        "contact_digest": "anon-1",
        "consent_to_follow_up": False,
    }
    one = client.post("/api/catalog/feedback", json=feedback_payload).json()
    two = client.post("/api/catalog/feedback", json=feedback_payload).json()
    assert one["id"] == two["id"]
