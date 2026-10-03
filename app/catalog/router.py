from __future__ import annotations

from fastapi import APIRouter, Query

from app.catalog.schemas import EvidenceReview, EvidenceSubmit, FeedbackSubmit, ProductCreate, ProductUpdate, SiteCreate, SiteUpdate
from app.catalog.insights import CatalogInsights
from app.catalog.service import CatalogService
from app.database import get_connection


router = APIRouter(prefix="/api/catalog", tags=["深度旅行目录"])


def service() -> CatalogService:
    return CatalogService()


@router.post("/products", status_code=201)
def create_product(payload: ProductCreate):
    return service().create_product(payload.model_dump())


@router.patch("/products/{code}")
def update_product(code: str, payload: ProductUpdate):
    return service().update_product(code, payload.model_dump())


@router.get("/products")
def list_products(category: str | None = None, status: str | None = None, active_only: bool = True, limit: int = Query(default=100, ge=1, le=500)):
    return {"items": service().list_products(category, status, active_only, limit)}


@router.post("/sites", status_code=201)
def create_site(payload: SiteCreate):
    return service().create_site(payload.model_dump())


@router.patch("/sites/{code}")
def update_site(code: str, payload: SiteUpdate):
    return service().update_site(code, payload.model_dump())


@router.get("/sites")
def list_sites(status: str | None = None, site_type: str | None = None, capability: str | None = None):
    return {"items": service().list_sites(status, site_type, capability)}


@router.post("/evidence", status_code=201)
def submit_evidence(payload: EvidenceSubmit):
    return service().submit_evidence(payload.model_dump())


@router.post("/evidence/{evidence_id}/review")
def review_evidence(evidence_id: int, payload: EvidenceReview):
    return service().review_evidence(evidence_id, payload.reviewer, payload.decision, payload.note)


@router.get("/evidence")
def list_evidence(product_code: str | None = None, status: str | None = None):
    return {"items": service().list_evidence(product_code, status)}


@router.post("/feedback", status_code=201)
def submit_feedback(payload: FeedbackSubmit):
    return service().submit_feedback(payload.model_dump())


@router.get("/feedback/summary")
def feedback_summary(product_code: str | None = None):
    return {"items": service().feedback_summary(product_code)}


@router.get("/insights/products/{product_code}/readiness")
def product_readiness(product_code: str):
    return CatalogInsights(get_connection()).product_readiness(product_code)


@router.get("/insights/products/{product_code}/feedback-tags")
def feedback_tags(product_code: str, limit: int = Query(default=20, ge=1, le=100)):
    return CatalogInsights(get_connection()).feedback_tags(product_code, limit)


@router.get("/insights/sites/{site_code}/utilization")
def site_utilization(site_code: str):
    return CatalogInsights(get_connection()).site_utilization(site_code)


@router.get("/insights/portfolio")
def portfolio_summary():
    return CatalogInsights(get_connection()).portfolio_summary()
